"""document_samples 表：文档格式样本（人工样例学习）。

用户主动上传一份"清晰、典型"的文档作为某类型的格式样本，
系统本地 OCR/解析后提取版式指纹（关键词标签、字符 N-gram、字段锚点），
后续遇到同版式的新文档即可直接自动归档，省人工、省 token。

与 recognition_templates 的区别：
- RecognitionTemplate：确认归档时自动积累的"字段级记性"（document_type -> 分类 + 关键字段）
- DocumentSample：人工主动标注的"版式级记性"（文档指纹 -> 类型 + 分类），匹配基于内容结构
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class DocumentSample(Base, TimestampMixin):
    __tablename__ = "document_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 匹配的文档类型（如"销售合同"）
    document_type: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # 归档分类路径（如 合同/销售合同）
    category_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # 样本原始文件名
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 版式指纹（JSON：labels / grams / fields / stats）
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 来源文档 id（若样本同时也是文档库中文件）
    sample_doc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 已自动归档命中次数
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def fingerprint_dict(self) -> dict:
        import json

        try:
            data = json.loads(self.fingerprint or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
