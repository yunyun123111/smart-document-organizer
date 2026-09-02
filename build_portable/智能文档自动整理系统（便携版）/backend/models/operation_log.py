"""operation_logs 表：文件操作日志，支持撤销审计。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.document import Document

# 操作类型枚举
OP_IMPORT = "IMPORT"
OP_PARSE = "PARSE"
OP_OCR = "OCR"
OP_CLASSIFY = "CLASSIFY"
OP_RENAME = "RENAME"
OP_MOVE = "MOVE"
OP_ARCHIVE = "ARCHIVE"
OP_USER_EDIT = "USER_EDIT"
OP_DELETE = "DELETE"
OP_UNDO = "UNDO"
ALL_OPERATION_TYPES = {
    OP_IMPORT,
    OP_PARSE,
    OP_OCR,
    OP_CLASSIFY,
    OP_RENAME,
    OP_MOVE,
    OP_ARCHIVE,
    OP_USER_EDIT,
    OP_DELETE,
    OP_UNDO,
}

# 操作结果
RESULT_OK = "ok"
RESULT_FAILED = "failed"


class OperationLog(Base, TimestampMixin):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    old_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    new_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    old_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    new_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    result: Mapped[str] = mapped_column(String(20), nullable=False, default=RESULT_OK)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    document: Mapped[Optional["Document"]] = relationship(back_populates="operation_logs")
