"""recycle_bin 表：回收站记录（V1.5-01 删除安全化）。

设计要点（与项目现有风格一致）：
- 与 documents 采用应用层关联（document_id），不引入数据库级外键（与现状一致）
- 删除进入回收站时**保留 documents 记录**（字段/分类/OCR/AI 结果/哈希均可恢复），
  仅将 documents.status 置为 recycled 并从正常文档库/搜索/统计中排除
- recycle_path 保存回收站内文件的实际位置，恢复/永久删除据此定位
- original_status 保存删除前状态，恢复时还原（如 archived 恢复后仍为 archived）
- original_file_missing 标记删除时原文件已不存在的情况（记录仍进回收站，不崩溃）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class RecycleBinItem(Base, TimestampMixin):
    __tablename__ = "recycle_bin"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 应用层关联原文档记录（无外键，与现有表一致）
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    current_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    original_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    current_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # 回收站内文件实际路径（恢复 / 永久删除定位）
    recycle_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    document_type: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 归档分类路径（如 合同/销售合同），原路径失效时可据此提示用户
    category_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # 删除前状态（恢复时还原）
    original_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # 删除原因（人工填写 / 批量删除 / 审核移除等）
    deleted_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 删除时原文件是否已不存在（文件移动成功才算完整删除；缺文件仅记录）
    original_file_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 进入回收站时间
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False, index=True
    )
