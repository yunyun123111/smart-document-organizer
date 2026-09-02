"""操作日志服务（规格书第六节 / Phase 12）。

所有文件操作必须记录：
- 数据库 operation_logs 表（供查询与撤销）
- data/logs/operations.log 文件（独立审计）
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from backend.models import (
    RESULT_FAILED,
    RESULT_OK,
    OperationLog,
)
from backend.utils.logger import get_operation_logger, get_logger

logger = get_logger("services.operation_service")
op_logger = get_operation_logger()


def log_operation(
    db: Session,
    operation_type: str,
    old_path: str = "",
    new_path: str = "",
    document_id: int | None = None,
    job_id: int | None = None,
    result: str = RESULT_OK,
    error_message: str = "",
) -> OperationLog:
    """写入一条操作日志（数据库 + 文件）。返回创建的记录。"""
    # 空路径保持为空：Path("") 会变成 "."，直接 str() 会污染审计数据
    old_p = Path(old_path) if old_path else None
    new_p = Path(new_path) if new_path else None
    entry = OperationLog(
        document_id=document_id,
        job_id=job_id,
        operation_type=operation_type,
        old_filename=old_p.name if old_p else "",
        new_filename=new_p.name if new_p else "",
        old_path=str(old_p) if old_p else "",
        new_path=str(new_p) if new_p else "",
        result=result,
        error_message=error_message,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # 文件操作日志（独立审计文件）
    status = "OK" if result == RESULT_OK else f"FAILED({error_message})"
    op_logger.info(
        "%s | %s -> %s | %s",
        operation_type,
        old_p or "(none)",
        new_p or "(none)",
        status,
    )
    return entry


def list_logs(db: Session, limit: int = 100, offset: int = 0, operation_type: str | None = None):
    """查询操作日志（倒序）。"""
    q = db.query(OperationLog).order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
    if operation_type:
        q = q.filter(OperationLog.operation_type == operation_type)
    return q.offset(offset).limit(limit).all()
