"""业务线台账对账结果表（SCHEMA V6）。

设计：一条业务线一行，差异明细以 JSON 存在 report_json，
避免"一条差异一行"导致列表查询要 GROUP BY、也无法承载候选单据等结构。

与 `business_records` 的关系是并列的：档案仍按合同号建，对账按业务线建，
通过 up_contract_no / down_contract_no 双路匹配，不做主键改造。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

# ---- 对账状态（优先级 error > warning > no_data > ok）----
RECON_STATUS_OK = "ok"
RECON_STATUS_WARNING = "warning"
RECON_STATUS_ERROR = "error"
# 整条业务线一份单据资料都没上传：此时 2×5 全空，谈不上"缺件"，更谈不上
# "有差异"。单独一档，避免 70 多条没资料的业务线全红、把真差异埋掉。
RECON_STATUS_NO_DATA = "no_data"
ALL_RECON_STATUSES: tuple[str, ...] = (
    RECON_STATUS_OK,
    RECON_STATUS_WARNING,
    RECON_STATUS_ERROR,
    RECON_STATUS_NO_DATA,
)

# ---- 差异类型 ----
DIFF_MISSING_DOC = "MISSING_DOC"            # 单据缺失
DIFF_AMOUNT_MISMATCH = "AMOUNT_MISMATCH"    # 单侧三单金额不符
DIFF_INTEREST_MISMATCH = "INTEREST_MISMATCH"  # 上下游结算差额与应收利息不符
DIFF_CHAIN_BREAK = "CHAIN_BREAK"            # 单据合同号不属于该业务线
DIFF_DATE_CONFLICT = "DATE_CONFLICT"        # 日期时序倒置
DIFF_DUPLICATE_BIZ = "DUPLICATE_BIZ"        # 发票号重复 / 文档挂多条业务线
DIFF_LEDGER_MISSING = "LEDGER_MISSING"      # 台账缺该业务线的结算/收付款行
DIFF_UNPARSED_AMOUNT = "UNPARSED_AMOUNT"    # 单据有金额但识别失败
DIFF_UNCLASSIFIED_DOC = "UNCLASSIFIED_DOC"  # 文档角色/方向无法判定

ALL_DIFF_TYPES: tuple[str, ...] = (
    DIFF_MISSING_DOC,
    DIFF_AMOUNT_MISMATCH,
    DIFF_INTEREST_MISMATCH,
    DIFF_CHAIN_BREAK,
    DIFF_DATE_CONFLICT,
    DIFF_DUPLICATE_BIZ,
    DIFF_LEDGER_MISSING,
    DIFF_UNPARSED_AMOUNT,
    DIFF_UNCLASSIFIED_DOC,
)

# ---- 严重级别（与 dossier_anomaly 的取值保持一致）----
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

# ---- 单据方向 ----
DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_UNKNOWN = "unknown"

RECON_TOLERANCE = Decimal("0.01")


class ReconciliationReport(Base, TimestampMixin):
    """一条业务线的对账快照（每次对账覆盖写）。"""

    __tablename__ = "reconciliation_reports"
    __table_args__ = (
        UniqueConstraint("business_no", name="ux_reconciliation_business_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 业务线号（台账主表 O 列），对账主键
    business_no: Mapped[str] = mapped_column(String(255), nullable=False)

    # 台账来源 sheet：云创总合同 / 物流合同（两套编号体系并行，不合并）
    ledger_sheet: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # 上下游合同号（同时用于反查合同档案）
    up_contract_no: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    down_contract_no: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # 结算口径（结算数据 sheet：M 合计 / P）
    up_settlement: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    down_settlement: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )

    # 利息（宏达收付款台账新 sheet：R 应收 / S 实收）
    interest_receivable: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    interest_received: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )

    # ok / warning / error
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RECON_STATUS_OK, index=True
    )
    diff_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 差异明细 + 10 格矩阵 + 候选单据
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    checked_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )
