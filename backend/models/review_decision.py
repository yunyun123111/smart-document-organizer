"""review_decisions 表：人工审核决策留痕。

为什么单独建表而不是塞 OperationLog：
- OperationLog 是"文件动了哪里"的操作审计（旧名/新路径），不带识别语义；
- 分类学指标（自动归档率、人工改判率、改判后能否沉淀规则）需要"AI 建议 vs 人工最终"
  的成对数据，且要在确认前捕获，否则 suggested_category 会被用户选择覆盖，永久丢失。

写入时机：所有确认/跳过路径（单条、批量、指纹簇批量）统一走 review_support.record_decision，
保证口径一致。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

# 决策动作
DECISION_APPROVE = "approve"
DECISION_SKIP = "skip"
ALL_DECISION_ACTIONS = {DECISION_APPROVE, DECISION_SKIP}

# 触发来源
SOURCE_SINGLE = "single"
SOURCE_BATCH = "batch"
SOURCE_CLUSTER = "cluster"
ALL_DECISION_SOURCES = {SOURCE_SINGLE, SOURCE_BATCH, SOURCE_CLUSTER}


class ReviewDecision(Base, TimestampMixin):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    action: Mapped[str] = mapped_column(String(20), nullable=False, default=DECISION_APPROVE)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_SINGLE)

    # AI/规则当时给的建议（确认前捕获，不随后续覆盖而变）
    suggested_category: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    suggested_document_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 人工最终选定的值
    final_category: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    final_document_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    category_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    type_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 本次确认是否沉淀了格式样本 / 候选规则
    sample_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rule_candidate_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
