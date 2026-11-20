"""候选规则 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RuleCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    keyword: str
    category_path: str
    document_type: Optional[str] = None
    from_doc_id: Optional[int] = None
    from_decision_id: Optional[int] = None
    status: str
    adopted_rule_id: Optional[int] = None
    hit_total: int = 0
    hit_target: int = 0
    note: str = ""
    created_at: Optional[datetime] = None
