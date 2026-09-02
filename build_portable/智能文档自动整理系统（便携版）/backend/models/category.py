"""categories 表：文档分类，支持无限层级（parent_id 自关联）。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.filename_rule import FilenameRule
    from backend.models.rename_template import RenameTemplate
    from backend.models.rule import Rule


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 自关联：子分类
    children: Mapped[list["Category"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Optional["Category"]] = relationship(
        back_populates="children", remote_side="Category.id"
    )

    rules: Mapped[list["Rule"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )
    rename_templates: Mapped[list["RenameTemplate"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )
    filename_rules: Mapped[list["FilenameRule"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )
