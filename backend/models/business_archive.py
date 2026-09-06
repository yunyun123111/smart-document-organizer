"""business_records / business_files 表：V2.0 业务档案模块。

业务档案把同一业务（如 YC0453）下的销售合同、采购合同、结算单、货权转移单、
发票等物理文件聚合为一个业务单元，支撑业务维度检索、完整性检查与时间线。

设计要点：
- 遵循项目 SQLAlchemy 2.0 风格（Mapped[] + mapped_column，不用旧 Column()）
- business_no 唯一索引：一个业务一条档案
- (business_id, document_id) 唯一约束：同一文件只能挂在一个档案下一次
- business_files 使用数据库级外键 ON DELETE CASCADE（配合 SQLite PRAGMA
  foreign_keys=ON 实际生效）；ORM 侧再叠加 cascade="all, delete-orphan" 双保险
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

# ---- 业务档案状态 ----
BUSINESS_STATUS_ACTIVE = "active"
BUSINESS_STATUS_COMPLETED = "completed"
BUSINESS_STATUS_ARCHIVED = "archived"
ALL_BUSINESS_STATUSES: tuple[str, ...] = (
    BUSINESS_STATUS_ACTIVE,
    BUSINESS_STATUS_COMPLETED,
    BUSINESS_STATUS_ARCHIVED,
)

# ---- 文件角色 ----
FILE_ROLE_CONTRACT = "contract"
FILE_ROLE_SETTLEMENT = "settlement"
FILE_ROLE_INVOICE = "invoice"
FILE_ROLE_CARGO_RIGHT = "cargo_right"
FILE_ROLE_OTHER = "other"
ALL_FILE_ROLES: tuple[str, ...] = (
    FILE_ROLE_CONTRACT,
    FILE_ROLE_SETTLEMENT,
    FILE_ROLE_INVOICE,
    FILE_ROLE_CARGO_RIGHT,
    FILE_ROLE_OTHER,
)

# ---- 关联来源 ----
LINK_SOURCE_AUTO_RULE = "auto_rule"
LINK_SOURCE_EXCEL_LEDGER = "excel_ledger"
LINK_SOURCE_MANUAL = "manual"
ALL_LINK_SOURCES: tuple[str, ...] = (
    LINK_SOURCE_AUTO_RULE,
    LINK_SOURCE_EXCEL_LEDGER,
    LINK_SOURCE_MANUAL,
)


class BusinessRecord(Base, TimestampMixin):
    """业务档案主表：一个业务（合同号）聚合其全部关联文件。"""

    __tablename__ = "business_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 业务编号 / 合同号（如 YC0453），唯一索引
    business_no: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 业务类型（如 销售/采购/混合）
    business_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # 状态：active / completed / archived
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BUSINESS_STATUS_ACTIVE, index=True
    )
    # 船名（业务常用维度，索引支持按船聚合）
    ship_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    counterparty: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    sign_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 扩展信息 JSON 文本（业务阶段、备注等，文本型便于后续演进不迁移）
    extra_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # 档案下全部关联文件；数据库级级联 + ORM 级联双保险
    files: Mapped[list["BusinessFile"]] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BusinessFile(Base):
    """业务档案与物理文件（documents 记录）的关联表。"""

    __tablename__ = "business_files"
    __table_args__ = (
        # 同一文件不能重复挂到同一档案
        UniqueConstraint("business_id", "document_id", name="ux_business_files_business_document"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("business_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 文件角色：contract / settlement / invoice / cargo_right / other
    file_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FILE_ROLE_OTHER
    )
    # 是否主单据（0/1）：一个档案建议一张主单据（如销售合同）
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 关联来源：auto_rule / excel_ledger / manual
    link_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LINK_SOURCE_MANUAL
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    business: Mapped["BusinessRecord"] = relationship(back_populates="files")
