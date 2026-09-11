"""dossier_anomalies 表：业务档案异常检测记录（V2.5 基础）。

业务异常检测的落库表，覆盖四类异常：
- MISSING_DOC           缺失单据：档案应包含的合同/结算单/发票/付款凭证缺失
- AMOUNT_MISMATCH       金额不符：合同金额 vs 结算金额存在较大差异
- DATE_CONFLICT         日期冲突：付款日期早于合同日期等时间关系异常
- QUANTITY_DEVIATION    数量偏差：合同数量 vs 结算数量偏差

设计要点：
- business_id 外键 → business_records ON DELETE CASCADE
- severity 分级：INFO / WARNING / CRITICAL
- related_document_ids 用 JSON 类型存关联异常单据 ID 列表
- is_resolved 支持人工忽略/解决（异常中心可手动关闭提醒）
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

# ---- 异常类型 ----
ANOMALY_MISSING_DOC = "MISSING_DOC"
ANOMALY_AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
ANOMALY_DATE_CONFLICT = "DATE_CONFLICT"
ANOMALY_QUANTITY_DEVIATION = "QUANTITY_DEVIATION"
ALL_ANOMALY_TYPES: tuple[str, ...] = (
    ANOMALY_MISSING_DOC,
    ANOMALY_AMOUNT_MISMATCH,
    ANOMALY_DATE_CONFLICT,
    ANOMALY_QUANTITY_DEVIATION,
)

# ---- 严重级别 ----
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"
ALL_SEVERITIES: tuple[str, ...] = (
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SEVERITY_CRITICAL,
)


class DossierAnomaly(Base, TimestampMixin):
    """业务档案异常记录：一条档案可有多条异常，支持人工解决。"""

    __tablename__ = "dossier_anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 所属业务档案
    business_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("business_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 异常类型：MISSING_DOC / AMOUNT_MISMATCH / DATE_CONFLICT / QUANTITY_DEVIATION
    anomaly_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 严重级别：INFO / WARNING / CRITICAL
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SEVERITY_WARNING
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 关联异常单据 ID 列表（JSON，如 [12, 34]）
    related_document_ids: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=list
    )
    # 是否已人工忽略/解决
    is_resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    business_record: Mapped["BusinessRecord"] = relationship(backref="anomalies")
