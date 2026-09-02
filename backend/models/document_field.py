"""document_fields 表：文档识别字段（合同编号、公司、日期、金额等）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.document import Document

# 字段来源枚举
SOURCE_RULE = "RULE"
SOURCE_OCR = "OCR"
SOURCE_AI = "AI"
SOURCE_USER = "USER"
SOURCE_FILENAME = "FILENAME"
ALL_SOURCES = {SOURCE_RULE, SOURCE_OCR, SOURCE_AI, SOURCE_USER, SOURCE_FILENAME}


class DocumentField(Base, TimestampMixin):
    __tablename__ = "document_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    field_value: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_RULE)

    document: Mapped["Document"] = relationship(back_populates="fields")
