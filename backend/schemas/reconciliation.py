"""业务线对账相关 Pydantic V2 模型（V6）。"""
from __future__ import annotations

from datetime import datetime
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
    """忽略某条差异。diff_type + index 定位（index 为该类型在 diffs 中的序号）。"""

    diff_type: str
    index: int = 0


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
