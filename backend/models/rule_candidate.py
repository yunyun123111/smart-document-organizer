"""rule_candidates 表：人工改判沉淀出的"候选规则"。

场景：AI 把某份单据分到 A，人工确认时改到 B。这是一次明确的纠错信号，
若这类文件靠文件名前缀就能识别（如 HSYCXS=销售合同），应当固化成
FilenameRule，下次零解析直接归档。

为什么不在改判时直接建规则：
- 自动改识别行为风险太高（FilenameRule 命中会跳过 OCR/AI 直接归档）；
- 需要先让用户看到"这条规则会影响库里多少份、其中多少份已在目标分类"再决定。

因此候选默认 pending，采纳动作必须由人触发。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

# 候选类型（当前只做文件名前缀）
KIND_FILENAME_PREFIX = "filename_prefix"
ALL_CANDIDATE_KINDS = {KIND_FILENAME_PREFIX}

# 状态
CANDIDATE_PENDING = "pending"
CANDIDATE_ADOPTED = "adopted"
CANDIDATE_REJECTED = "rejected"
ALL_CANDIDATE_STATUSES = {CANDIDATE_PENDING, CANDIDATE_ADOPTED, CANDIDATE_REJECTED}


class RuleCandidate(Base, TimestampMixin):
    __tablename__ = "rule_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    kind: Mapped[str] = mapped_column(String(30), nullable=False, default=KIND_FILENAME_PREFIX)
    # 文件名前缀（作为 FilenameRule.pattern 使用，正则语义）
    keyword: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category_path: Mapped[str] = mapped_column(String(500), nullable=False)
    document_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # 来源文档（第一次触发该候选的改判记录）
    from_doc_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    from_decision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CANDIDATE_PENDING)
    adopted_rule_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 生成时实测的命中情况（不是预测，是当前库扫描结果）
    hit_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hit_target: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
