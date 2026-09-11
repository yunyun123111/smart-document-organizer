"""document_fingerprints 表：文档指纹（智能批处理基础，V1.5 规划落地）。

记录每份文档的版面布局哈希与结构关键词 SimHash，用于：
- 同类文件自动聚类（版面相似 → 同一模板 → 批量免审）
- 与识别模板绑定（同一版面 → 直接套用模板字段提取）
- 疑似重复检测（内容相同但文件名不同）

设计要点：
- document_id 唯一：一份文档一条指纹（UNIQUE）
- layout_hash 版面坐标密度哈希 + text_simhash 结构关键词 64 位哈希，双索引
- feature_meta 存 JSON 文本（文本块数、表格数、宽高比等补充特征）
- matched_template_id 可选外键 → recognition_templates，ON DELETE SET NULL
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class DocumentFingerprint(Base):
    """文档指纹：版面 + 结构特征，支撑批量聚类免审。"""

    __tablename__ = "document_fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 一份文档一条指纹
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # 版面坐标密度哈希（布局指纹）
    layout_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 结构关键词 SimHash（64 位）
    text_simhash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 补充特征元数据（JSON 文本：文本块数、表格数、宽高比等）
    feature_meta: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 绑定的识别模板 ID（可选，命中模板后回填；模板删除时置空）
    matched_template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recognition_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )
