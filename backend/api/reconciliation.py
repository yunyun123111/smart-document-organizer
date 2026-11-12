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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    ALL_RECON_STATUSES,
    RECON_STATUS_ERROR,
    RECON_STATUS_NO_DATA,
    RECON_STATUS_OK,
    RECON_STATUS_WARNING,
    ReconciliationReport,
    SEVERITY_CRITICAL,
)
from backend.schemas.reconciliation import (
    DiffOut,
    MatrixCellOut,
    ReconciliationDetailOut,
    ReconciliationListResponse,
    ReconciliationReportOut,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
    ResolveDiffRequest,
    UnlinkedDocOut,
    UnlinkedListResponse,
)
from backend.services.reconciliation_service import (
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
        unlinked_document_ids=payload.get("unlinked_document_ids", []),
    )


@router.post("/reports/{business_no}/resolve", response_model=ReconciliationReportOut)
def resolve_diff(business_no: str, payload: ResolveDiffRequest, db: Session = Depends(get_db)):
    """忽略一条差异（不删除记录，标记 ignored 并重算状态）。"""
    row = db.execute(
        select(ReconciliationReport).where(ReconciliationReport.business_no == business_no)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到该业务线对账结果：{business_no}")

    data = json.loads(row.report_json or "{}")
    diffs = data.get("diffs", [])
    matched = [d for d in diffs if d.get("type") == payload.diff_type]
    if not matched:
        raise HTTPException(status_code=404, detail=f"该业务线没有类型为 {payload.diff_type} 的差异")
    if payload.index >= len(matched):
        raise HTTPException(status_code=400, detail="差异序号超出范围")

    target = matched[payload.index]
    target["ignored"] = True

    data["diffs"] = diffs
    row.report_json = json.dumps(data, ensure_ascii=False)
    status, count = _recompute_status(diffs, _payload_has_docs(data))
    row.status = status
    row.diff_count = count
    db.commit()
    db.refresh(row)
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
