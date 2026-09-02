"""文件名规则 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class FilenameRuleCreate(BaseModel):
    category_id: int
    pattern: str
    priority: int = 0
    enabled: bool = True
    note: Optional[str] = None


class FilenameRuleUpdate(BaseModel):
    category_id: Optional[int] = None
    pattern: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


class FilenameRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    pattern: str
    priority: int
    enabled: bool
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    category_name: str = ""
    category_path: str = ""
