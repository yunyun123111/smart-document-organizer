"""业务线对账相关 Pydantic V2 模型（V6）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DiffOut(BaseModel):
    """一条差异。"""

    type: str
    direction: str = ""
    role: str = ""
    severity: str = "WARNING"
    title: str = ""
    expected: str = ""
    actual: str = ""
    delta: str = ""
    message: str = ""
    document_ids: list[int] = Field(default_factory=list)
    candidate_document_ids: list[int] = Field(default_factory=list)
    ignored: bool = False
    # 差异闭环：稳定标识 / 是否本轮新增（None=首次对账无基准）/ 处理记录
    key: str = ""
    is_new: Optional[bool] = None
    resolution: dict = Field(default_factory=dict)


class ResolvedDiffOut(BaseModel):
    """上一轮存在、本轮已消失的差异（补单/调账后消失）。"""

    key: str = ""
    type: str = ""
    direction: str = ""
    role: str = ""
    title: str = ""
    expected: str = ""
    actual: str = ""
    resolved_at: str = ""


class MatrixCellOut(BaseModel):
    """单据矩阵单格（方向 × 角色）。"""

    direction: str
    role: str
    count: int = 0
    amount: float = 0.0
    document_ids: list[int] = Field(default_factory=list)
    candidate_count: int = 0


class ReconciliationReportOut(BaseModel):
    """对账结果（列表）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_no: str
    ledger_sheet: str = ""
    up_contract_no: str = ""
    down_contract_no: str = ""
    up_settlement: float = 0.0
    down_settlement: float = 0.0
    interest_receivable: float = 0.0
    interest_received: float = 0.0
    status: str = "ok"
    diff_count: int = 0
    checked_at: Optional[datetime] = None


class ReconciliationDetailOut(ReconciliationReportOut):
    """对账详情：带矩阵、差异、台账原始口径。"""

    vessel: str = ""
    material: str = ""
    supplier: str = ""
    customer: str = ""
    note: str = ""
    up_contract_amount: float = 0.0
    down_contract_amount: float = 0.0
    matrix: list[MatrixCellOut] = Field(default_factory=list)
    diffs: list[DiffOut] = Field(default_factory=list)
    resolved_diffs: list[ResolvedDiffOut] = Field(default_factory=list)
    unlinked_document_ids: list[int] = Field(default_factory=list)


class ReconciliationListResponse(BaseModel):
    total: int = 0
    items: list[ReconciliationReportOut] = Field(default_factory=list)


class ReconciliationRunRequest(BaseModel):
    """触发对账。business_no 为空则全量。"""

    business_no: Optional[str] = None
    source_id: Optional[int] = None


class ReconciliationRunResponse(BaseModel):
    total: int = 0
    ok: int = 0
    warning: int = 0
    error: int = 0
    no_data: int = 0
    unlinked: int = 0
    ledger_sheets: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    message: str = ""


class ResolveDiffRequest(BaseModel):
    """处理一条差异。

    - key 优先（run 时生成的稳定标识）；没有 key 的旧数据回退到 diff_type + index。
    - resolution：ignore / false_positive 视为关闭（不计入差异数）；
      supplement / adjust / pending 只记进度，仍计入未处理差异。
    """

    diff_type: str = ""
    index: int = 0
    key: str = ""
    resolution: str = "ignore"
    note: str = ""


class UnlinkedDocOut(BaseModel):
    """未归属文档。"""

    document_id: int
    filename: str = ""
    document_type: str = ""
    contract_no: str = ""
    role: str = "other"
    direction: str = "unknown"
    reason: str = ""


class UnlinkedListResponse(BaseModel):
    total: int = 0
    items: list[UnlinkedDocOut] = Field(default_factory=list)


# ==================== P1：外部明细 / 催办 / 聚合 ====================


class ExternalDetailOut(BaseModel):
    """外部明细一行。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_no: str = ""
    business_no: str = ""
    occurred_on: Optional[date] = None
    amount: float = 0.0
    kind: str = "receipt"
    note: str = ""
    source_filename: str = ""
    imported_at: Optional[datetime] = None


class ExternalDetailListResponse(BaseModel):
    total: int = 0
    receipt_total: float = 0.0
    payment_total: float = 0.0
    items: list[ExternalDetailOut] = Field(default_factory=list)


class ExternalBatchOut(BaseModel):
    batch_no: str = ""
    source_filename: str = ""
    imported_at: str = ""
    rows: int = 0
    receipt: str = "0.00"
    payment: str = "0.00"


class ExternalBatchListResponse(BaseModel):
    total: int = 0
    items: list[ExternalBatchOut] = Field(default_factory=list)


class ExternalImportResponse(BaseModel):
    batch_no: str = ""
    imported: int = 0
    skipped: int = 0
    source_filename: str = ""
    columns: dict = Field(default_factory=dict)
    message: str = ""


class UrgeRequest(BaseModel):
    """催办一条业务线的待补资料。"""

    note: str = ""


class UrgeOut(BaseModel):
    at: str = ""
    note: str = ""


class UrgeResponse(BaseModel):
    business_no: str
    count: int = 0
    last_at: str = ""
    message: str = ""


class UrgeItemOut(BaseModel):
    """催办清单一行：这条业务线缺什么、催过几次。"""

    business_no: str = ""
    customer: str = ""
    period: str = ""
    status: str = ""
    missing_roles: list[str] = Field(default_factory=list)
    requested_since: str = ""
    urge_count: int = 0
    last_urged_at: str = ""
    last_note: str = ""


class UrgeListResponse(BaseModel):
    total: int = 0
    items: list[UrgeItemOut] = Field(default_factory=list)


class SummaryRowOut(BaseModel):
    """客户 / 期间视角的一行聚合。"""

    key: str = ""
    label: str = ""
    business_count: int = 0
    up_settlement: float = 0.0
    down_settlement: float = 0.0
    interest_received: float = 0.0
    pay_amount: float = 0.0
    receipt_amount: float = 0.0
    external_receipt: float = 0.0
    diff_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    no_data_count: int = 0


class SummaryResponse(BaseModel):
    group: str = "customer"
    total: int = 0
    rows: list[SummaryRowOut] = Field(default_factory=list)


class ReconcileScheduleOut(BaseModel):
    enabled: bool = False
    cron: str = ""
    last_run_at: str = ""
    last_result: str = ""
    changed_businesses: list[str] = Field(default_factory=list)
    last_error: str = ""


class ReconcileScheduleRequest(BaseModel):
    enabled: Optional[bool] = None
    cron: Optional[str] = None


class InvoiceUniquenessOut(BaseModel):
    """发票号跨业务线重复。"""

    invoice_no: str = ""
    business_nos: list[str] = Field(default_factory=list)
    title: str = ""


class InvoiceUniquenessResponse(BaseModel):
    total: int = 0
    items: list[InvoiceUniquenessOut] = Field(default_factory=list)
