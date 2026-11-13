"""业务线台账自动对账 API（V6）。

端点：
- POST /run                       触发对账（全量或单条业务线）
- GET  /reports                   对账结果列表（状态/关键词筛选、分页）
- GET  /reports/{business_no}     详情（10 格矩阵 + 差异）
- POST /reports/{business_no}/resolve  忽略某条差异
- GET  /unlinked                  未归属文档清单
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    ALL_RECON_STATUSES,
    DIFF_MISSING_DOC,
    KIND_PAYMENT,
    RECON_STATUS_ERROR,
    RECON_STATUS_NO_DATA,
    RECON_STATUS_OK,
    RECON_STATUS_WARNING,
    ReconciliationReport,
    SEVERITY_CRITICAL,
)
from backend.schemas.reconciliation import (
    DiffOut,
    ExternalBatchListResponse,
    ExternalBatchOut,
    ExternalDetailListResponse,
    ExternalDetailOut,
    ExternalImportResponse,
    InvoiceUniquenessOut,
    InvoiceUniquenessResponse,
    MatrixCellOut,
    ReconciliationDetailOut,
    ReconciliationListResponse,
    ReconcileScheduleOut,
    ReconcileScheduleRequest,
    ReconciliationReportOut,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
    ResolvedDiffOut,
    ResolveDiffRequest,
    SummaryResponse,
    SummaryRowOut,
    UnlinkedDocOut,
    UnlinkedListResponse,
    UrgeItemOut,
    UrgeListResponse,
    UrgeRequest,
    UrgeResponse,
)
from backend.services.external_detail_service import (
    ExternalDetailError,
    delete_batch,
    import_details,
    list_batches,
    list_details,
    mapping_from_json,
)
from backend.services.reconciliation_export import build_workbook, export_filename
from backend.services.reconciliation_service import (
    ALL_RESOLUTIONS,
    RESOLUTION_CLOSES,
    RESOLUTION_REOPEN,
    ReconciliationError,
    ReconciliationService,
)
from backend.utils.logger import get_logger

logger = get_logger("api.reconciliation")

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

_STATUS_ORDER = {
    RECON_STATUS_ERROR: 0,
    RECON_STATUS_WARNING: 1,
    RECON_STATUS_OK: 2,
    # 没上传资料的沉底：它不是"待处理差异"，是"还没开始"
    RECON_STATUS_NO_DATA: 3,
}


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _recompute_status(diffs: list[dict], has_docs: bool = True) -> tuple[str, int]:
    active = [d for d in diffs if not d.get("ignored")]
    if not has_docs:
        return RECON_STATUS_NO_DATA, len(active)
    if any(d.get("severity") == SEVERITY_CRITICAL for d in active):
        return RECON_STATUS_ERROR, len(active)
    if any(d.get("severity") == SEVERITY_WARNING for d in active):
        return RECON_STATUS_WARNING, len(active)
    # 只剩 INFO：金额已勾稽通过，INFO 是"已核对过、无需处理"的说明
    return RECON_STATUS_OK, len(active)


def _payload_has_docs(data: dict) -> bool:
    """该业务线是否关联到了任何单据（矩阵任一格份数 > 0）。"""
    return any(int(cell.get("count") or 0) > 0 for cell in data.get("matrix", []))


@router.post("/run", response_model=ReconciliationRunResponse)
def run_reconciliation(payload: ReconciliationRunRequest, db: Session = Depends(get_db)):
    """执行对账。business_no 为空则跑全部业务线。"""
    try:
        result = ReconciliationService(db).run(
            business_no=payload.business_no, source_id=payload.source_id
        )
    except ReconciliationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReconciliationRunResponse(**result, message="对账完成")


@router.get("/reports", response_model=ReconciliationListResponse)
def list_reports(
    status: Optional[str] = Query(default=None, description="ok / warning / error"),
    keyword: Optional[str] = Query(default=None, description="业务线号或合同号"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """对账结果列表。未跑过对账时返回空列表。"""
    stmt = select(ReconciliationReport)
    if status:
        if status not in ALL_RECON_STATUSES:
            raise HTTPException(status_code=400, detail=f"未知状态：{status}")
        stmt = stmt.where(ReconciliationReport.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(
            ReconciliationReport.business_no.like(like),
            ReconciliationReport.up_contract_no.like(like),
            ReconciliationReport.down_contract_no.like(like),
        ))

    rows = db.execute(stmt).scalars().all()
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 3), -r.id))
    total = len(rows)
    start = (page - 1) * page_size
    items = [
        ReconciliationReportOut.model_validate(row)
        for row in rows[start:start + page_size]
    ]
    return ReconciliationListResponse(total=total, items=items)


@router.get("/export")
def export_reports(
    status: Optional[str] = Query(default=None, description="ok / warning / error / no_data"),
    keyword: Optional[str] = Query(default=None, description="业务线号或合同号"),
    db: Session = Depends(get_db),
):
    """导出对账结果 Excel（对账明细 / 差异明细 / 待补资料 / 未归属单据）。

    只读快照，不重跑对账；筛选条件与列表页一致。
    """
    if status and status not in ALL_RECON_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知状态：{status}")

    stmt = select(ReconciliationReport)
    if status:
        stmt = stmt.where(ReconciliationReport.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(
            ReconciliationReport.business_no.like(like),
            ReconciliationReport.up_contract_no.like(like),
            ReconciliationReport.down_contract_no.like(like),
        ))

    rows = db.execute(stmt).scalars().all()
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 3), -r.id))

    buf = build_workbook(rows)
    logger.info("导出对账结果：%d 条业务线", len(rows))
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{export_filename()}"'},
    )


@router.get("/reports/{business_no}", response_model=ReconciliationDetailOut)
def get_report_detail(business_no: str, db: Session = Depends(get_db)):
    """业务线对账详情：10 格矩阵 + 差异明细。"""
    row = db.execute(
        select(ReconciliationReport).where(ReconciliationReport.business_no == business_no)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到该业务线对账结果：{business_no}")

    payload = json.loads(row.report_json or "{}")
    matrix = [MatrixCellOut(**cell) for cell in payload.get("matrix", [])]
    diffs = [DiffOut(**{**d}) for d in payload.get("diffs", [])]

    return ReconciliationDetailOut(
        id=row.id,
        business_no=row.business_no,
        ledger_sheet=row.ledger_sheet,
        up_contract_no=row.up_contract_no,
        down_contract_no=row.down_contract_no,
        up_settlement=_to_float(row.up_settlement),
        down_settlement=_to_float(row.down_settlement),
        interest_receivable=_to_float(row.interest_receivable),
        interest_received=_to_float(row.interest_received),
        status=row.status,
        diff_count=row.diff_count,
        checked_at=row.checked_at,
        vessel=payload.get("vessel", ""),
        material=payload.get("material", ""),
        supplier=payload.get("supplier", ""),
        customer=payload.get("customer", ""),
        note=payload.get("note", ""),
        up_contract_amount=_to_float(payload.get("up_contract_amount")),
        down_contract_amount=_to_float(payload.get("down_contract_amount")),
        matrix=matrix,
        diffs=diffs,
        resolved_diffs=[ResolvedDiffOut(**d) for d in payload.get("resolved_diffs", [])],
        unlinked_document_ids=payload.get("unlinked_document_ids", []),
    )


@router.post("/reports/{business_no}/resolve", response_model=ReconciliationReportOut)
def resolve_diff(business_no: str, payload: ResolveDiffRequest, db: Session = Depends(get_db)):
    """处理一条差异（P0-2 差异闭环）。

    处理方式：
    - ignore / false_positive：关闭，不再计入差异数
    - supplement（待补单）/ adjust（待调账）/ pending（待确认）：只记进度，仍计入
    - reopen：撤销之前的处理，重新待办

    定位优先用 run 时生成的稳定 `key`，旧快照回退到 diff_type + index。
    """
    if payload.resolution not in ALL_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"未知处理方式：{payload.resolution}")

    row = db.execute(
        select(ReconciliationReport).where(ReconciliationReport.business_no == business_no)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到该业务线对账结果：{business_no}")

    data = json.loads(row.report_json or "{}")
    diffs = data.get("diffs", [])
    if not diffs:
        raise HTTPException(status_code=404, detail="该业务线当前没有差异")

    target = None
    if payload.key:
        target = next((d for d in diffs if d.get("key") == payload.key), None)
    if target is None and payload.diff_type:
        matched = [d for d in diffs if d.get("type") == payload.diff_type]
        if payload.index >= len(matched):
            raise HTTPException(status_code=400, detail="差异序号超出范围")
        target = matched[payload.index]
    if target is None:
        raise HTTPException(status_code=404, detail="没有匹配的差异（刷新后重试）")

    if payload.resolution == RESOLUTION_REOPEN:
        target["resolution"] = {}
        target["ignored"] = False
    else:
        target["resolution"] = {
            "type": payload.resolution,
            "note": payload.note.strip(),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        target["ignored"] = payload.resolution in RESOLUTION_CLOSES

    data["diffs"] = diffs
    row.report_json = json.dumps(data, ensure_ascii=False)
    status, count = _recompute_status(diffs, _payload_has_docs(data))
    row.status = status
    row.diff_count = count
    db.commit()
    db.refresh(row)
    logger.info("处理差异：%s %s -> %s", business_no, target.get("key", ""), payload.resolution)
    return ReconciliationReportOut.model_validate(row)


@router.get("/unlinked", response_model=UnlinkedListResponse)
def list_unlinked(
    source_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """未归属文档：合同号为空、或不属于任何业务线。"""
    try:
        items = ReconciliationService(db).list_unlinked(source_id=source_id)
    except ReconciliationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UnlinkedListResponse(
        total=len(items),
        items=[UnlinkedDocOut(**item) for item in items],
    )


# ==================== P1：外部明细 / 催办 / 客户期间视角 ====================


@router.post("/external-details/import", response_model=ExternalImportResponse)
def import_external_details(
    file: UploadFile = File(...),
    mapping: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """导入外部明细表（xlsx/csv 的第一个 sheet）。

    - 表头默认按「业务线号 / 金额 / 日期 / 类型 / 备注」常见别名识别；
    - 识别不到时可传 mapping（JSON，target→列名，如 {"business_no":"业务编号"}）；
    - 只作参考口径，不参与对账判定。
    """
    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    tmp_dir = tempfile.mkdtemp(prefix="ext_detail_")
    tmp_path = Path(tmp_dir) / f"upload{suffix}"
    try:
        with open(tmp_path, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        result = import_details(
            db,
            tmp_path,
            filename=file.filename or tmp_path.name,
            mapping=mapping_from_json(mapping),
        )
    except ExternalDetailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info("导入外部明细：%s 行，批次 %s", result["imported"], result["batch_no"])
    return ExternalImportResponse(**result)


@router.get("/external-details", response_model=ExternalDetailListResponse)
def list_external_details(
    business_no: str = Query(default="", description="业务线号，留空看全部"),
    batch_no: str = Query(default="", description="批次号"),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """外部明细清单（按业务线/批次筛选），并返回筛选范围的收付合计。"""
    rows = list_details(db, business_no=business_no, batch_no=batch_no, limit=limit)
    receipt = sum((r.amount for r in rows if r.kind != KIND_PAYMENT), Decimal("0"))
    payment = sum((r.amount for r in rows if r.kind == KIND_PAYMENT), Decimal("0"))
    return ExternalDetailListResponse(
        total=len(rows),
        receipt_total=float(receipt),
        payment_total=float(payment),
        items=[ExternalDetailOut.model_validate(r) for r in rows],
    )


@router.get("/external-details/batches", response_model=ExternalBatchListResponse)
def list_external_batches(db: Session = Depends(get_db)):
    """已导入的批次（谁、什么时候、多少行）。"""
    items = list_batches(db)
    return ExternalBatchListResponse(total=len(items), items=[ExternalBatchOut(**i) for i in items])


@router.delete("/external-details/batches/{batch_no}")
def delete_external_batch(batch_no: str, db: Session = Depends(get_db)):
    """整批删除（导错了重来）。"""
    removed = delete_batch(db, batch_no)
    if not removed:
        raise HTTPException(status_code=404, detail=f"未找到批次：{batch_no}")
    logger.info("删除外部明细批次 %s：%d 行", batch_no, removed)
    return {"batch_no": batch_no, "removed": removed}


@router.post("/reports/{business_no}/urge", response_model=UrgeResponse)
def urge_business(business_no: str, payload: UrgeRequest, db: Session = Depends(get_db)):
    """催办：记一条催办记录到该业务线的报告 payload（urges）。

    没有单独建表——催办是报告的一个附注，跟着报告版本走，
    重跑对账时原样保留（不清空），避免"催过又忘了"。
    """
    row = db.execute(
        select(ReconciliationReport).where(ReconciliationReport.business_no == business_no)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到该业务线对账结果：{business_no}")

    data = json.loads(row.report_json or "{}")
    urges = data.get("urges") or []
    at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    urges.append({"at": at, "note": payload.note.strip()})
    data["urges"] = urges
    row.report_json = json.dumps(data, ensure_ascii=False)
    db.commit()
    logger.info("催办记录：%s（累计 %d 次）", business_no, len(urges))
    return UrgeResponse(
        business_no=business_no,
        count=len(urges),
        last_at=at,
        message=f"已记录第 {len(urges)} 次催办",
    )


@router.get("/urge-list", response_model=UrgeListResponse)
def list_urge_items(db: Session = Depends(get_db)):
    """缺件催办清单：所有还缺资料、且未关闭的业务线。"""
    rows = db.execute(select(ReconciliationReport)).scalars().all()
    items: list[UrgeItemOut] = []
    for row in rows:
        data = json.loads(row.report_json or "{}")
        missing = [
            d for d in data.get("diffs", [])
            if d.get("type") == DIFF_MISSING_DOC and not d.get("ignored")
        ]
        if not missing:
            continue
        roles = [d.get("role") or d.get("title") or "缺失资料" for d in missing]
        urges = data.get("urges") or []
        items.append(UrgeItemOut(
            business_no=row.business_no,
            customer=data.get("customer", ""),
            period=data.get("period", ""),
            status=row.status,
            missing_roles=roles,
            requested_since=row.checked_at.strftime("%Y-%m-%d %H:%M") if row.checked_at else "",
            urge_count=len(urges),
            last_urged_at=urges[-1]["at"] if urges else "",
            last_note=urges[-1].get("note", "") if urges else "",
        ))
    items.sort(key=lambda x: (-x.urge_count, x.business_no))
    return UrgeListResponse(total=len(items), items=items)


@router.get("/summary", response_model=SummaryResponse)
def summary_by_group(
    group: str = Query(default="customer", pattern="^(customer|month)$"),
    db: Session = Depends(get_db),
):
    """客户 / 期间视角聚合：按采购商或月份汇总金额、差异数。"""
    if group not in {"customer", "month"}:
        raise HTTPException(status_code=400, detail="group 只支持 customer / month")

    rows = db.execute(select(ReconciliationReport)).scalars().all()
    buckets: dict[str, SummaryRowOut] = {}
    for row in rows:
        data = json.loads(row.report_json or "{}")
        if group == "customer":
            key = (data.get("customer") or "").strip() or "未填写采购商"
            label = key
        else:
            key = (data.get("period") or "").strip() or "未填日期"
            label = key

        item = buckets.setdefault(key, SummaryRowOut(key=key, label=label))
        item.business_count += 1
        item.up_settlement += _to_float(data.get("up_settlement"))
        item.down_settlement += _to_float(data.get("down_settlement"))
        item.interest_received += _to_float(data.get("interest_received"))
        item.pay_amount += _to_float(data.get("pay_amount"))
        item.receipt_amount += _to_float(data.get("receipt_amount"))
        item.external_receipt += _to_float(data.get("external_receipt_total"))
        item.diff_count += int(row.diff_count or 0)
        if row.status == RECON_STATUS_ERROR:
            item.error_count += 1
        elif row.status == RECON_STATUS_WARNING:
            item.warning_count += 1
        elif row.status == RECON_STATUS_NO_DATA:
            item.no_data_count += 1

    ordered = sorted(buckets.values(), key=lambda x: x.error_count * 10 + x.warning_count, reverse=True)
    return SummaryResponse(group=group, total=len(ordered), rows=ordered)


@router.get("/invoice-uniqueness", response_model=InvoiceUniquenessResponse)
def invoice_uniqueness(db: Session = Depends(get_db)):
    """发票号跨业务线唯一性校验（P2）。

    同一张发票不应出现在两条业务线：要么重复归档，要么归属错了。
    只查最近一次对账里挂了单据的业务线。
    """
    rows = db.execute(select(ReconciliationReport)).scalars().all()
    invoices: dict[str, set[str]] = {}
    business_of_doc: dict[int, str] = {}
    for row in rows:
        data = json.loads(row.report_json or "{}")
        for cell in data.get("matrix", []):
            for doc_id in cell.get("document_ids") or []:
                business_of_doc[int(doc_id)] = row.business_no
    # 发票号在文档字段里，不在对账结果里
    from backend.models import DocumentField

    for doc_id, value in db.execute(
        select(DocumentField.document_id, DocumentField.field_value)
        .where(DocumentField.field_name == "invoice_no")
    ).all():
        no = str(value or "").strip().upper()
        if len(no) < 6:
            continue
        business = business_of_doc.get(int(doc_id))
        if business:
            invoices.setdefault(no, set()).add(business)
    items = [
        InvoiceUniquenessOut(
            invoice_no=no,
            business_nos=sorted(bns),
            title=f"发票号 {no} 同时出现在 {len(bns)} 条业务线",
        )
        for no, bns in invoices.items() if len(bns) > 1
    ]
    items.sort(key=lambda x: x.invoice_no)
    return InvoiceUniquenessResponse(total=len(items), items=items)


@router.get("/schedule", response_model=ReconcileScheduleOut)
def get_reconcile_schedule():
    """定时巡检配置与最近一次结果（含变化提醒）。"""
    from backend.services.reconciliation_scheduler import get_schedule

    return ReconcileScheduleOut(**get_schedule())


@router.put("/schedule", response_model=ReconcileScheduleOut)
def update_reconcile_schedule(payload: ReconcileScheduleRequest):
    """开启/关闭定时巡检，或改执行时刻（cron 只支持「分 时 * * *」）。"""
    from backend.services.reconciliation_scheduler import set_schedule

    try:
        data = set_schedule(enabled=payload.enabled, cron=payload.cron)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReconcileScheduleOut(**data)


@router.post("/schedule/clear-changes")
def clear_reconcile_changes():
    """看过变化提醒后手动清空（避免红点一直挂着）。"""
    from backend.services.reconciliation_scheduler import clear_changes, get_schedule

    clear_changes()
    return {"changed_businesses": get_schedule().get("changed_businesses", [])}
