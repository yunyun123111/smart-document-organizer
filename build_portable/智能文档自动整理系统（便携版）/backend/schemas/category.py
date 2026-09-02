"""分类相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None
    description: str = ""
    sort_order: int = 0
    enabled: bool = True


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    enabled: Optional[bool] = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: Optional[int] = None
    name: str
    path: str
    description: str
    enabled: bool
    sort_order: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CategoryTreeOut(CategoryOut):
    """带子分类的树形结构。"""
    children: list["CategoryTreeOut"] = []
