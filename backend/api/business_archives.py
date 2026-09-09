"""业务档案 API（V2.0）。

提供档案列表（分页/筛选/关键词/文件数统计）、详情（含文件列表）、
手动创建/更新、手动挂文档、解绑、历史数据回填。
鉴权：走现有 /api 前缀 + Bearer 访问密码中间件。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    ALL_BUSINESS_STATUSES,
    BusinessFile,
    BusinessRecord,
    Document,
    FILE_ROLE_OTHER,
)
from backend.schemas.business_archive import (
    BackfillResponse,
    BusinessFileAddRequest,
    BusinessFileOut,
    BusinessRecordCreate,
    BusinessRecordDetailResponse,
    BusinessRecordListResponse,
    BusinessRecordOut,
    BusinessRecordUpdate,
)
from backend.services.business_archive_service import (
    BusinessArchiveError,
    BusinessArchiveService,
    _resolve_file_role,
    compute_completeness,
)
from backend.utils.logger import get_logger

logger = get_logger("api.business_archives")

router = APIRouter(prefix="/api/business-archives", tags=["business-archives"])

_service = BusinessArchiveService()


# ---------------- 工具函数 ----------------


def _to_record_out(
    record: BusinessRecord, file_count: int = 0, roles_csv: str = ""
) -> BusinessRecordOut:
    roles = {r for r in (roles_csv or "").split(",") if r}
    return BusinessRecordOut(
        id=record.id,
        business_no=record.business_no,
        title=record.title,
        business_type=record.business_type,
        status=record.status,
        ship_name=record.ship_name,
        counterparty=record.counterparty,
        total_amount=float(record.total_amount or 0),
        sign_date=record.sign_date,
        extra_data=record.extra_data,
        created_at=record.created_at,
        updated_at=record.updated_at,
        file_count=file_count,
        completeness=compute_completeness(roles),
    )


def _load_file_outs(db: Session, business_id: int) -> list[BusinessFileOut]:
    """档案下全部关联文件（join documents 带出文件名/类型）。"""
    rows = db.execute(
        select(
            BusinessFile,
            Document.original_filename,
            Document.document_type,
            Document.file_type,
        )
        .join(Document, Document.id == BusinessFile.document_id)
        .where(BusinessFile.business_id == business_id)
        .order_by(BusinessFile.sort_order, BusinessFile.id)
    ).all()
    outs: list[BusinessFileOut] = []
    for bf, filename, doc_type, file_type in rows:
        out = BusinessFileOut.model_validate(bf)
        out.document_filename = filename or ""
        out.document_type = doc_type or ""
        out.file_type = file_type or ""
        outs.append(out)
    return outs


def _file_count(db: Session, business_id: int) -> int:
    """档案关联文件数（独立查询，避免 ORM relationship 缓存导致计数为 0）。"""
    return (
        db.execute(
            select(func.count())
            .select_from(BusinessFile)
            .where(BusinessFile.business_id == business_id)
        ).scalar()
        or 0
    )


def _get_or_404(db: Session, business_id: int) -> BusinessRecord:
    record = _service.get_business(db, business_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"业务档案不存在 business_id={business_id}")
    return record


# ---------------- 列表 / 详情 ----------------


@router.get("", response_model=BusinessRecordListResponse)
def list_business_archives(
    status: Optional[str] = None,
    business_type: Optional[str] = None,
    keyword: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """档案列表：分页 + 按状态/业务类型筛选 + 关键词（编号/标题/船名/对方公司模糊）。"""
    file_count_sq = (
        select(func.count())
        .select_from(BusinessFile)
        .where(BusinessFile.business_id == BusinessRecord.id)
        .correlate(BusinessRecord)
        .scalar_subquery()
    )
    roles_sq = (
        select(func.group_concat(BusinessFile.file_role, ","))
        .select_from(BusinessFile)
        .where(BusinessFile.business_id == BusinessRecord.id)
        .correlate(BusinessRecord)
        .scalar_subquery()
    )
    query = db.query(
        BusinessRecord, file_count_sq.label("file_count"), roles_sq.label("roles_csv")
    )

    if status:
        query = query.filter(BusinessRecord.status == status)
    if business_type:
        query = query.filter(BusinessRecord.business_type.like(f"%{business_type}%"))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                BusinessRecord.business_no.like(kw),
                BusinessRecord.title.like(kw),
                BusinessRecord.ship_name.like(kw),
                BusinessRecord.counterparty.like(kw),
            )
        )

    total = query.count()
    rows = (
        query.order_by(BusinessRecord.id.desc()).offset(skip).limit(limit).all()
    )
    items = [
        _to_record_out(record, file_count, roles_csv or "")
        for record, file_count, roles_csv in rows
    ]
    logger.info("查询业务档案列表: total=%d returned=%d", total, len(items))
    return BusinessRecordListResponse(total=total, items=items)


@router.get("/{business_id}", response_model=BusinessRecordDetailResponse)
def get_business_archive(business_id: int, db: Session = Depends(get_db)):
    """档案详情：基本信息 + 名下全部关联文件。"""
    record = _get_or_404(db, business_id)
    roles = set(
        db.execute(
            select(BusinessFile.file_role).where(BusinessFile.business_id == business_id)
        ).scalars()
    )
    out = _to_record_out(record, file_count=_file_count(db, business_id))
    out.completeness = compute_completeness(roles)
    out.files = _load_file_outs(db, business_id)
    return BusinessRecordDetailResponse(data=out)


# ---------------- 创建 / 更新 ----------------


@router.post("", response_model=BusinessRecordDetailResponse, status_code=201)
def create_business_archive(payload: BusinessRecordCreate, db: Session = Depends(get_db)):
    """手动创建档案。business_no 唯一，重复返回 409。"""
    business_no = payload.business_no.strip()
    if _service.get_business_by_no(db, business_no) is not None:
        raise HTTPException(status_code=409, detail=f"业务编号已存在: {business_no}")

    status_val = payload.status or "active"
    if status_val not in ALL_BUSINESS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法状态: {status_val}（可选: {', '.join(ALL_BUSINESS_STATUSES)}）",
        )
    record = BusinessRecord(
        business_no=business_no,
        title=payload.title or f"{business_no} 业务档案",
        business_type=payload.business_type or "",
        status=status_val,
        ship_name=payload.ship_name or "",
        counterparty=payload.counterparty or "",
        total_amount=payload.total_amount if payload.total_amount is not None else 0,
        sign_date=payload.sign_date,
        extra_data=payload.extra_data or "{}",
    )
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("创建业务档案失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"创建失败: {exc}") from exc
    logger.info("手动创建业务档案: %s (id=%s)", record.business_no, record.id)
    return BusinessRecordDetailResponse(data=_to_record_out(record))


@router.put("/{business_id}", response_model=BusinessRecordDetailResponse)
def update_business_archive(
    business_id: int, payload: BusinessRecordUpdate, db: Session = Depends(get_db)
):
    """更新档案基础信息（只更新传入字段）。"""
    record = _get_or_404(db, business_id)
    updates = payload.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] not in ALL_BUSINESS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法状态: {updates['status']}（可选: {', '.join(ALL_BUSINESS_STATUSES)}）",
        )
    for field, value in updates.items():
        if value is not None:
            setattr(record, field, value)
    try:
        db.commit()
        db.refresh(record)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("更新业务档案失败: business_id=%s: %s", business_id, exc)
        raise HTTPException(status_code=500, detail=f"更新失败: {exc}") from exc
    logger.info("更新业务档案: id=%s fields=%s", business_id, sorted(updates))
    return BusinessRecordDetailResponse(
        data=_to_record_out(record, file_count=_file_count(db, business_id))
    )


# ---------------- 关联文件 ----------------


@router.post("/{business_id}/files", response_model=BusinessRecordDetailResponse, status_code=201)
def add_file_to_business(
    business_id: int, payload: BusinessFileAddRequest, db: Session = Depends(get_db)
):
    """手动把文档挂到档案。file_role 缺省时按文档类型自动映射。"""
    record = _get_or_404(db, business_id)
    document = db.get(Document, payload.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"文档不存在 document_id={payload.document_id}")

    file_role = payload.file_role or _resolve_file_role(document.document_type)
    try:
        _service.manual_link_file(db, business_id, payload.document_id, file_role)
    except BusinessArchiveError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = _get_or_404(db, business_id)
    out = _to_record_out(record, file_count=_file_count(db, business_id))
    out.files = _load_file_outs(db, business_id)
    logger.info("挂载文档到档案: business_id=%s document_id=%s role=%s", business_id, payload.document_id, file_role)
    return BusinessRecordDetailResponse(data=out)


@router.delete("/{business_id}/files/{document_id}")
def remove_file_from_business(business_id: int, document_id: int, db: Session = Depends(get_db)):
    """解绑文档（不删除物理文档与 documents 记录）。"""
    record = _get_or_404(db, business_id)
    removed = _service.unlink_file(db, business_id, document_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"文档 {document_id} 未关联到档案 {business_id}")
    logger.info("解绑文档: business_id=%s document_id=%s", business_id, document_id)
    return {"ok": True, "business_id": business_id, "document_id": document_id}


# ---------------- 解散档案 ----------------


@router.delete("/{business_id}")
def dissolve_business_archive(business_id: int, db: Session = Depends(get_db)):
    """解散档案：删除档案及全部关联（business_files），文档本身保留在文档库。"""
    record = _get_or_404(db, business_id)
    try:
        unlinked = _service.dissolve_business(db, business_id)
    except BusinessArchiveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info(
        "解散档案: id=%s business_no=%s 解绑=%d", business_id, record.business_no, unlinked
    )
    return {
        "ok": True,
        "business_id": business_id,
        "business_no": record.business_no,
        "unlinked_documents": unlinked,
        "message": f"档案 {record.business_no} 已解散，文档保留在文档库",
    }


# ---------------- 历史回填 ----------------


@router.post("/backfill", response_model=BackfillResponse)
def backfill_business_archives(db: Session = Depends(get_db)):
    """扫描历史文档，批量建立业务档案并自动归集。"""
    stats = _service.backfill_historical_data(db)
    logger.info("历史数据回填完成: %s", stats)
    return BackfillResponse(stats=stats)
