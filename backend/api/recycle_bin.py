"""回收站 API（V1.5-01 删除安全化）。

所有删除操作默认进入回收站，本模块提供回收站的查看 / 恢复 / 永久删除 / 清空。
鉴权：走现有 /api 前缀 + Bearer 访问密码中间件。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import RecycleBinItem
from backend.services.recycle_service import recycle_service
from backend.utils.logger import get_logger

logger = get_logger("api.recycle_bin")

router = APIRouter(prefix="/api/recycle-bin", tags=["recycle-bin"])


class BatchIdsRequest(BaseModel):
    ids: list[int]


@router.get("")
def list_recycle_bin(db: Session = Depends(get_db)):
    """回收站列表（含原分类/删除原因/文件是否存在等）。"""
    return {"ok": True, "items": recycle_service.list_items(db)}


@router.post("/batch-restore")
def batch_restore(req: BatchIdsRequest, db: Session = Depends(get_db)):
    """批量恢复：逐条恢复，单条失败不中断。"""
    ok, failed = 0, 0
    errors: list[str] = []
    restored: list[int] = []
    for rid in req.ids:
        item = db.get(RecycleBinItem, rid)
        if item is None:
            failed += 1
            errors.append(f"回收站记录#{rid} 不存在")
            continue
        res = recycle_service.restore(db, item)
        if res.success:
            ok += 1
            restored.append(rid)
        else:
            failed += 1
            errors.append(f"#{rid}: {res.error}")
    return {"ok": True, "restored_count": ok, "failed_count": failed, "restored": restored, "errors": errors}


@router.post("/{item_id}/restore")
def restore_item(item_id: int, db: Session = Depends(get_db)):
    """恢复单个回收站记录。"""
    item = db.get(RecycleBinItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="回收站记录不存在")
    res = recycle_service.restore(db, item)
    if not res.success:
        raise HTTPException(status_code=400, detail=res.error)
    return {"ok": True, "message": res.message}


@router.post("/batch-delete")
def batch_delete(req: BatchIdsRequest, db: Session = Depends(get_db)):
    """批量永久删除：逐条处理，单条失败不中断。"""
    ok, failed = 0, 0
    errors: list[str] = []
    for rid in req.ids:
        item = db.get(RecycleBinItem, rid)
        if item is None:
            failed += 1
            errors.append(f"回收站记录#{rid} 不存在")
            continue
        res = recycle_service.permanent_delete(db, item)
        if res.success:
            ok += 1
        else:
            failed += 1
            errors.append(f"#{rid}: {res.error}")
    return {"ok": True, "deleted_count": ok, "failed_count": failed, "errors": errors}


@router.delete("/{item_id}")
def permanent_delete_item(item_id: int, db: Session = Depends(get_db)):
    """永久删除单个回收站记录（物理删除文件 + 记录）。"""
    item = db.get(RecycleBinItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="回收站记录不存在")
    res = recycle_service.permanent_delete(db, item)
    if not res.success:
        raise HTTPException(status_code=400, detail=res.error)
    return {"ok": True, "message": res.message}


@router.post("/empty")
def empty_recycle_bin(db: Session = Depends(get_db)):
    """清空回收站：逐条永久删除，返回成功/失败数。"""
    result = recycle_service.empty(db)
    return {"ok": True, **result}
