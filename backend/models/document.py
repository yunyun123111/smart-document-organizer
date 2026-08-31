"""documents 表：文件档案主表。"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.document_field import DocumentField
    from backend.models.operation_log import OperationLog

# 文件状态枚举值（规格书第十章）
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_NEED_REVIEW = "need_review"
STATUS_FAILED = "failed"
STATUS_ARCHIVED = "archived"
STATUS_DUPLICATE = "duplicate"
STATUS_SKIPPED = "skipped"

ALL_STATUSES = {
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_PROCESSED,
    STATUS_NEED_REVIEW,
    STATUS_FAILED,
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_SKIPPED,
}


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    current_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    original_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    current_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    file_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)

    document_type: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_PENDING, index=True
    )

    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 关系
    fields: Mapped[list["DocumentField"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentField.id",
    )
    operation_logs: Mapped[list["OperationLog"]] = relationship(back_populates="document")
