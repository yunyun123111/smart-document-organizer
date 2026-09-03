"""回收站服务（V1.5-01 删除安全化）。

设计原则：
- 任何删除默认进入回收站，不再直接物理删除
- 进入回收站时**保留 documents 记录**（字段/分类/OCR/AI/哈希均可恢复），
  仅将 documents.status 置为 recycled，并从正常文档库/搜索/统计中排除
- 文件本体安全移动到 data/recycle_bin/{年}/{月}/，文件名加 document_id 前缀防冲突
- 恢复时还原原状态、原路径；同名绝不覆盖（递增后缀）
- 永久删除是唯一真正物理删除文件的通道，同时清理 recycle_bin 记录与 documents 记录
  （document_fields 级联删除；operation_logs 因 FK SET NULL 保留审计）
- 异常安全：文件移动失败不落库；落库失败回搬文件；原文件缺失仅标记不崩溃
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import (
    OP_DELETE_TO_RECYCLE,
    OP_PERMANENT_DELETE,
    OP_RESTORE_FROM_RECYCLE,
    RESULT_FAILED,
    RESULT_OK,
    STATUS_RECYCLED,
    Document,
    RecycleBinItem,
)
from backend.services.file_service import FileOperationError, ensure_directory, move_file
from backend.services.operation_service import log_operation
from backend.utils.filename_utils import safe_filename
from backend.utils.logger import get_logger

logger = get_logger("services.recycle_service")


@dataclass
class RecycleResult:
    success: bool
    message: str = ""
    recycle_id: int | None = None
    file_moved: bool = False
    error: str = ""


def _recycle_target_dir(now: datetime | None = None) -> Path:
    """回收站目录：recycle_bin_root / 年 / 月。目录自动创建。"""
    now = now or datetime.now()
    return ensure_directory(settings.recycle_bin_root / str(now.year) / f"{now.month:02d}")


def _unique_target(target: Path) -> Path:
    """目标已存在时递增后缀（xxx_001.ext），绝不覆盖已有文件。"""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        candidate = target.with_name(f"{stem}_{i:03d}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


class RecycleService:
    def move_to_recycle(
        self,
        db: Session,
        document: Document,
        reason: str = "",
    ) -> RecycleResult:
        """把文档移入回收站。

        流程：定位文件 → 移动到回收站目录（唯一名）→ 建 recycle_bin 记录
        → documents.status=recycled → 记操作日志。
        异常安全：文件移动失败则不落库；落库失败回搬文件。
        原文件缺失：仅标记 original_file_missing，记录仍进回收站（不崩溃）。
        """
        src = Path(document.current_path or document.original_path or "")
        moved = False
        target: Path | None = None
        try:
            # ---- 1. 移动文件（若存在）----
            if src and src.is_file():
                target = _unique_target(
                    _recycle_target_dir() / f"{document.id}_{safe_filename(src.name)}"
                )
                move_file(src, target)  # 失败抛 FileOperationError，源文件不动
                moved = True
                recycle_path = str(target)
            else:
                recycle_path = ""

            # ---- 2. 建回收站记录 + 改状态 ----
            try:
                # 归档分类存在 document_fields 的 suggested_category 字段
                category_path = ""
                for f in document.fields:
                    if f.field_name == "suggested_category":
                        category_path = f.field_value or ""
                        break
                item = RecycleBinItem(
                    document_id=document.id,
                    original_filename=document.original_filename,
                    current_filename=document.current_filename,
                    original_path=document.original_path,
                    current_path=document.current_path,
                    recycle_path=recycle_path,
                    file_hash=document.file_hash,
                    file_size=document.file_size,
                    file_type=document.file_type,
                    document_type=document.document_type,
                    category_path=category_path,
                    original_status=document.status,
                    deleted_reason=reason or "用户删除",
                    original_file_missing=not moved,
                )
                db.add(item)
                document.status = STATUS_RECYCLED
                # log_operation 内部 commit：一并提交 pending 变更
                log_operation(
                    db,
                    OP_DELETE_TO_RECYCLE,
                    old_path=str(src),
                    new_path=str(target) if target else "",
                    document_id=document.id,
                    result=RESULT_OK,
                    error_message=reason or "删除进入回收站",
                )
                db.refresh(item)
            except Exception:
                # 落库失败：回搬已移动的文件，保持原状
                if moved and target is not None and target.exists():
                    try:
                        move_file(target, src)
                    except Exception:  # noqa: BLE001
                        logger.critical(
                            "回收站落库失败且回搬失败，文件位于 %s（源 %s）", target, src
                        )
                logger.exception("移入回收站落库失败: doc#%s", document.id)
                raise

            logger.info(
                "移入回收站: doc#%s %s -> %s (reason=%s)",
                document.id, src, target or "(文件缺失)", reason,
            )
            return RecycleResult(
                success=True,
                recycle_id=item.id,
                file_moved=moved,
                message="已移入回收站" + ("" if moved else "（原文件缺失，仅保留记录）"),
            )
        except FileOperationError as e:
            return RecycleResult(success=False, error=f"移入回收站失败：{e}")
        except Exception as e:  # noqa: BLE001
            return RecycleResult(success=False, error=f"移入回收站失败：{e}")

    def restore(self, db: Session, item: RecycleBinItem) -> RecycleResult:
        """从回收站恢复。

        流程：定位回收站文件 → 恢复回原路径（同名不覆盖，递增后缀）→
        删除回收站记录 → documents 还原原状态 → 记操作日志。
        """
        document = db.get(Document, item.document_id)
        if document is None:
            return RecycleResult(
                success=False, error=f"原文档记录不存在（doc#{item.document_id}），无法恢复"
            )
        # 回收站内文件已丢失：仍可恢复记录（保留原信息），提示文件缺失
        src = Path(item.recycle_path)
        file_missing = not src.is_file()

        # ---- 1. 恢复文件（若存在）----
        original_target = Path(item.original_path or item.current_path or "")
        restored_path: Path | None = None
        if not file_missing:
            if not original_target.name:
                original_target = Path(item.current_path or item.original_path or "")
            if not original_target.name:
                original_target = settings.document_root / (item.current_filename or item.original_filename)
            ensure_directory(original_target.parent)
            target = _unique_target(original_target)  # 同名绝不覆盖
            try:
                move_file(src, target)
                restored_path = target
            except FileOperationError as e:
                return RecycleResult(success=False, error=f"恢复文件失败：{e}")

        # ---- 2. 更新记录 ----
        try:
            if restored_path is not None:
                document.current_path = str(restored_path)
                document.current_filename = restored_path.name
            document.status = item.original_status or "pending"
            db.delete(item)
            log_operation(
                db,
                OP_RESTORE_FROM_RECYCLE,
                old_path=item.recycle_path,
                new_path=str(restored_path) if restored_path else "",
                document_id=document.id,
                result=RESULT_OK,
                error_message="从回收站恢复" + ("（原文件已丢失，仅恢复记录）" if file_missing else ""),
            )
        except Exception:
            # 记录更新失败：回搬文件到回收站
            if restored_path is not None and restored_path.exists():
                try:
                    move_file(restored_path, src)
                except Exception:  # noqa: BLE001
                    logger.critical(
                        "恢复落库失败且回搬失败，文件位于 %s（回收站 %s）", restored_path, src
                    )
            logger.exception("恢复落库失败: recycle#%s", item.id)
            raise

        logger.info(
            "从回收站恢复: doc#%s -> %s (原状态=%s%s)",
            document.id, restored_path or "(文件缺失)", item.original_status,
            "，文件缺失仅恢复记录" if file_missing else "",
        )
        return RecycleResult(
            success=True,
            message="已恢复" + ("" if not file_missing else "（原文件已丢失，仅恢复记录）"),
        )

    def permanent_delete(self, db: Session, item: RecycleBinItem) -> RecycleResult:
        """永久删除：唯一允许物理删除文件的通道。

        同时清理：回收站文件（存在则删）→ recycle_bin 记录 → documents 记录
        （document_fields 级联删除；operation_logs 因 FK SET NULL 保留审计）。
        """
        document = db.get(Document, item.document_id)
        try:
            src = Path(item.recycle_path)
            if src.is_file():
                src.unlink(missing_ok=True)
            # 先记永久删除日志（此时 document_id 仍有效），再删记录
            log_operation(
                db,
                OP_PERMANENT_DELETE,
                old_path=item.original_path,
                new_path=item.recycle_path,
                document_id=item.document_id,
                result=RESULT_OK,
                error_message="回收站永久删除",
            )
            db.delete(item)
            if document is not None:
                db.delete(document)  # document_fields 级联删除
            db.commit()
            logger.info("回收站永久删除: recycle#%s doc#%s", item.id, item.document_id)
            return RecycleResult(success=True, message="已永久删除")
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.exception("回收站永久删除失败: recycle#%s", item.id)
            return RecycleResult(success=False, error=f"永久删除失败：{e}")

    def empty(self, db: Session) -> dict:
        """清空回收站：逐条永久删除，单条失败不中断。返回成功/失败数。"""
        items = db.query(RecycleBinItem).all()
        ok, failed = 0, 0
        errors: list[str] = []
        for item in items:
            res = self.permanent_delete(db, item)
            if res.success:
                ok += 1
            else:
                failed += 1
                errors.append(f"#{(item.document_id or '?')}: {res.error}")
        return {"ok": ok, "failed": failed, "errors": errors}

    def list_items(self, db: Session) -> list[dict]:
        """回收站列表（含原分类/删除原因/文件大小等，倒序）。"""
        items = (
            db.query(RecycleBinItem)
            .order_by(RecycleBinItem.deleted_at.desc(), RecycleBinItem.id.desc())
            .all()
        )
        return [
            {
                "id": i.id,
                "document_id": i.document_id,
                "original_filename": i.original_filename,
                "current_filename": i.current_filename,
                "original_path": i.original_path,
                "recycle_path": i.recycle_path,
                "file_hash": i.file_hash,
                "file_size": i.file_size,
                "file_type": i.file_type,
                "document_type": i.document_type,
                "category_path": i.category_path,
                "original_status": i.original_status,
                "deleted_reason": i.deleted_reason,
                "original_file_missing": i.original_file_missing,
                "file_exists": Path(i.recycle_path).is_file() if i.recycle_path else False,
                "deleted_at": i.deleted_at.isoformat() if i.deleted_at else None,
            }
            for i in items
        ]


recycle_service = RecycleService()
