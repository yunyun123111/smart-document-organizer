"""recon_manual_links 表：人工把某份单据挂到某条业务线的指定槽位。

为什么需要这张表：
对账默认靠合同号 / 金额锚点 / 对手方名称自动归位（见
reconciliation_service._bind_documents）。但真实资料里大量单据自动归不了位：
没有合同号的发票、跨期回款回单、扫描件号段模糊、一张票对应多条业务。

人工指定必须落库。只在内存里指定，下一次 run 会被推算结果覆盖，用户会看到
"刚挂上的票又变回缺件"。这张表就是推算的上游：有记录的单据不再参与推断。

约束：(business_no, document_id) 唯一 —— 一份单据在一条业务线上只占一个槽位。
跨业务线分摊靠多行（business_no 不同），不靠一行的金额拆分。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

# 槽位方向（与 reconciliation_service.DIRECTION_* 对齐）
MANUAL_DIRECTION_UP = "up"
MANUAL_DIRECTION_DOWN = "down"
MANUAL_DIRECTION_UNKNOWN = "unknown"


class ReconManualLink(Base, TimestampMixin):
    """业务线 × 单据 × 槽位的人工归属记录。"""

    __tablename__ = "recon_manual_links"
    __table_args__ = (
        UniqueConstraint(
            "business_no", "document_id", name="ux_recon_manual_business_document"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 业务线号（台账 O 列），不关联 business_records：档案按合同号建、对账按业务线建
    business_no: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # up / down / unknown
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default=MANUAL_DIRECTION_UNKNOWN, index=True
    )
    # contract / settlement / invoice / cargo_right / payment_receipt
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    # 人工备注（为什么这么挂，便于以后复查）
    note: Mapped[str] = mapped_column(String(512), nullable=False, default="")
