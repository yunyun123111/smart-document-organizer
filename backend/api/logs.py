"""操作日志 API：列表 / 撤销。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import OperationLog
from backend.services.undo_service import undo_service
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/logs", tags=["logs"])
logger = get_logger("api.logs")


@router.get("")
def list_logs(
    operation_type: str | None = None,
    keyword: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(OperationLog).order_by(OperationLog.id.desc())
    if operation_type:
        q = q.filter(OperationLog.operation_type == operation_type)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((OperationLog.old_path.like(like)) | (OperationLog.new_path.like(like)))
    return q.offset(skip).limit(limit).all()


@router.post("/{log_id}/undo")
def undo_log(log_id: int, db: Session = Depends(get_db)):
    """按日志撤销一次操作（如归档恢复原位置）。"""
    log = db.get(OperationLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    result = undo_service.undo(db, log_id)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {"ok": True, "restored_path": str(result.restored_path) if result.restored_path else None}
