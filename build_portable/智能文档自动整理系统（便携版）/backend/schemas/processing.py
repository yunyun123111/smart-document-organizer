"""批量处理相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProcessingStartRequest(BaseModel):
    """开始整理请求。source_dir 为空则扫描配置的 inbox 目录。"""
    source_dir: Optional[str] = None


class ProcessingJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total_files: int
    processed_files: int
    success_count: int
    review_count: int
    failed_count: int
    duplicate_count: int
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class UploadResponse(BaseModel):
    document_id: int
    filename: str
    status: str
