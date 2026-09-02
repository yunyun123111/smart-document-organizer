"""filename_rules 表：文件名规则（按文件名直接归档，跳过内容识别）。

用于业务编码明确的文件（如 SJWLXS=销售合同、SJWLCG=采购合同），
命中即直接归档，零解析/零 OCR/零 AI，最大限度省 token 与耗时。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.category import Category


class FilenameRule(Base, TimestampMixin):
    __tablename__ = "filename_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 匹配模式（正则，大小写不敏感），如 "SJWLXS"
    pattern: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 说明（如 "SJWLXS=销售合同"）
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    category: Mapped["Category"] = relationship(back_populates="filename_rules")
