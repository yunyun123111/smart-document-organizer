"""Excel 登记表数据源：工作簿 + 各 sheet 的匹配配置。

把用户手工登记的 Excel 台账（合同/结算/收付款等）作为权威数据源：
OCR 识别出字段后，用合同号（或兜底键）匹配 Excel 行，
命中则用 Excel 行字段覆盖 OCR 结果，再走命名模板归档。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ExcelSource(Base, TimestampMixin):
    """一份已上传的 Excel 工作簿（可含多个 sheet 数据源）。"""

    __tablename__ = "excel_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    total_sheets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 关系：每个 sheet 一份配置（应用层关联，无数据库级外键）
    sheets: Mapped[list["ExcelSheetConfig"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="ExcelSheetConfig.id",
        primaryjoin="ExcelSheetConfig.source_id == ExcelSource.id",
        foreign_keys="ExcelSheetConfig.source_id",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "file_path": self.file_path,
            "enabled": self.enabled,
            "total_sheets": self.total_sheets,
            "sheets": [s.to_dict() for s in self.sheets],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExcelSheetConfig(Base, TimestampMixin):
    """某个 sheet 的匹配配置：启用状态、列映射、主键列。"""

    __tablename__ = "excel_sheet_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    doc_type_hint: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # 标准字段名 -> Excel 列名（JSON 字符串）；字段名与 field_extractor 对齐
    column_map: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 合同号所在列名（主键列）
    key_column: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    source: Mapped["ExcelSource"] = relationship(
        back_populates="sheets",
        primaryjoin="ExcelSheetConfig.source_id == ExcelSource.id",
        foreign_keys="ExcelSheetConfig.source_id",
    )

    def to_dict(self) -> dict:
        import json

        try:
            col_map = json.loads(self.column_map) if self.column_map else {}
        except Exception:  # noqa: BLE001
            col_map = {}
        return {
            "id": self.id,
            "source_id": self.source_id,
            "sheet_name": self.sheet_name,
            "enabled": self.enabled,
            "doc_type_hint": self.doc_type_hint,
            "column_map": col_map,
            "key_column": self.key_column,
            "row_count": self.row_count,
            "last_error": self.last_error,
        }
