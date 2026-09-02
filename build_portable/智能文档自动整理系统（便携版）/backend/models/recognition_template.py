"""recognition_templates 表：识别模板（同类文件"记性"）。

用户人工确认归档某类文件后，系统记录其 文档类型 -> 归档分类 -> 关键字段，
下次再遇到同类型且关键字段齐全的文件，直接按模板自动归档，
不再进人工审核、不再走 AI 兜底（省人工、省 token）。

仅做"分类 + 归档决策"的记忆；重命名模板仍复用 Category.rename_templates。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class RecognitionTemplate(Base, TimestampMixin):
    __tablename__ = "recognition_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 匹配的文档类型（如"结算单"）
    document_type: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # 归档分类路径（如 合同/结算单）
    category_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # 命中所要求的关键字段（JSON 数组，如 ["date","vessel","contract_no","material"]）
    require_fields: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 来源文档（第一次学习时的文档 id）
    created_from_doc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 已自动归档命中次数
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def require_fields_list(self) -> list[str]:
        import json

        try:
            data = json.loads(self.require_fields or "[]")
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []
