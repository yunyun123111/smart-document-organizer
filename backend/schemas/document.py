"""文档相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    current_filename: str
    file_type: str
    file_size: int
    document_type: str
    title: str
    confidence: Optional[float] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DocumentFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str
    field_value: str
    confidence: float
    source: str


class DocumentDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    current_filename: str
    original_path: str
    current_path: str
    file_type: str
    mime_type: str
    file_size: int
    file_hash: str
    document_type: str
    title: str
    confidence: Optional[float] = None
    status: str
    extracted_text: str
    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    fields: list[DocumentFieldOut] = Field(default_factory=list)


class DocumentUpdate(BaseModel):
    """人工审核时修改文档。"""
    document_type: Optional[str] = None
    title: Optional[str] = None
    category_path: Optional[str] = None
    fields: Optional[dict[str, str]] = None  # 字段名 -> 值


class ReviewApproveRequest(BaseModel):
    """确认归档请求。"""
    document_type: Optional[str] = None
    category_path: Optional[str] = None
    filename: Optional[str] = None  # 自定义文件名（可选）
    fields: Optional[dict[str, str]] = None
