"""规则与重命名模板 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RuleCreate(BaseModel):
    category_id: int
    keyword: str
    match_type: str = "contains"  # contains | exact | regex
    priority: int = 0
    weight: float = 1.0
    enabled: bool = True


class RuleUpdate(BaseModel):
    category_id: Optional[int] = None
    keyword: Optional[str] = None
    match_type: Optional[str] = None
    priority: Optional[int] = None
    weight: Optional[float] = None
    enabled: Optional[bool] = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    keyword: str
    match_type: str
    priority: int
    weight: float
    enabled: bool
    created_at: Optional[datetime] = None


class RenameTemplateCreate(BaseModel):
    category_id: int
    template: str
    enabled: bool = True


class RenameTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    template: str
    enabled: bool
    created_at: Optional[datetime] = None
