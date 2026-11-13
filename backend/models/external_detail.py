"""外部明细表（SCHEMA V7）。

用途：用户手里有些数据不在台账里，比如下游回款明细、银行流水导出。
台账只登记汇总数，明细在另一张表里——这类数据以前只能人工对，
现在导入本表后按业务线挂载，作为**参考口径**展示。

边界（重要）：
- 外部明细**不参与对账判定**，不生成差异、不改变状态。
  下游回款核对口径尚未确认（一个客户挂多条业务线、款拆不开），
  在口径确定前，明细只做展示与比对参考，避免用错误口径判"有差异"。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

KIND_RECEIPT = "receipt"   # 收款/回款
KIND_PAYMENT = "payment"   # 付款
ALL_KINDS: tuple[str, ...] = (KIND_RECEIPT, KIND_PAYMENT)


class ExternalDetail(Base, TimestampMixin):
    """一条外部明细（一次导入一个批次，批次内一行一笔）。"""

    __tablename__ = "external_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 批次号：同一次导入共享，删除时按批次整体删
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)

    # 业务线号：为空表示该行没能匹配到业务线，留在清单里待人工处理
    business_no: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)

    # 发生日期
    occurred_on: Mapped[date] = mapped_column(Date, nullable=True)

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )

    # receipt / payment
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=KIND_RECEIPT)

    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # 来源文件名，便于追溯是哪张表导进来的
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
