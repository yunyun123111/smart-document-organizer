"""撤销服务（规格书第六节 / Phase 12）。

根据操作日志把文件从 new_path 恢复到 old_path。
只支持可逆操作（ARCHIVE / MOVE / RENAME），且要求日志记录完整路径。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models import (
    OP_ARCHIVE,
    OP_MOVE,
    OP_RENAME,
    OP_UNDO,
    RESULT_FAILED,
    RESULT_OK,
    STATUS_PENDING,
    Document,
    OperationLog,
)
from backend.services import operation_service
from backend.services.file_service import FileOperationError, ensure_directory, move_file
from backend.utils.logger import get_logger

logger = get_logger("services.undo_service")

REVERSIBLE_OPS = {OP_ARCHIVE, OP_MOVE, OP_RENAME}


@dataclass
class UndoResult:
    success: bool
    restored_path: Path | None = None
    error: str = ""


class UndoService:
    def undo(self, db: Session, log_id: int) -> UndoResult:
        """撤销指定操作日志对应的文件移动。"""
        log = db.get(OperationLog, log_id)
        if log is None:
            return UndoResult(success=False, error=f"日志不存在: {log_id}")
        if log.operation_type not in REVERSIBLE_OPS:
            return UndoResult(
                success=False, error=f"操作不可撤销: {log.operation_type}"
            )
        if not log.new_path or not log.old_path:
            return UndoResult(success=False, error="日志缺少路径信息，无法撤销")

        new_path = Path(log.new_path)
        old_path = Path(log.old_path)

        if not new_path.exists():
            return UndoResult(success=False, error=f"文件不存在，无法撤销: {new_path}")
        if old_path.exists():
            return UndoResult(
                success=False, error=f"原位置已存在文件，撤销被阻止: {old_path}"
            )

        try:
            ensure_directory(old_path.parent)
            restored = move_file(new_path, old_path)
        except FileOperationError as e:
            operation_service.log_operation(
                db,
                OP_UNDO,
                old_path=str(new_path),
                new_path=str(old_path),
                document_id=log.document_id,
                job_id=log.job_id,
                result=RESULT_FAILED,
                error_message=str(e),
            )
            logger.error("撤销失败: %s", e)
            return UndoResult(success=False, error=str(e))

        operation_service.log_operation(
            db,
            OP_UNDO,
            old_path=str(new_path),
            new_path=str(restored),
            document_id=log.document_id,
            job_id=log.job_id,
            result=RESULT_OK,
        )
        self._restore_document_state(db, log, old_path)
        logger.info("撤销成功: %s -> %s", new_path, restored)
        return UndoResult(success=True, restored_path=restored)

    @staticmethod
    def _restore_document_state(db: Session, log: OperationLog, old_path: Path) -> None:
        """撤销后把文档档案改回归档前状态。

        否则 doc.status 仍是 archived、current_path 指向已不存在的归档路径：
        - 去重服务只认 status=archived，会让回到待整理目录的文件被误判为重复；
        - 文档库显示已归档但预览/下载 404。
        """
        if log.document_id is None:
            return
        doc = db.get(Document, log.document_id)
        if doc is None:
            return
        doc.status = STATUS_PENDING
        doc.current_path = str(old_path)
        doc.current_filename = old_path.name
        doc.processed_at = None
        db.commit()
        logger.info("撤销已回写文档状态: doc#%s -> pending (%s)", doc.id, old_path)


undo_service = UndoService()
