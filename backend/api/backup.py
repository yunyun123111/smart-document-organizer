"""备份 / 恢复 API（P0-3）。

- POST /api/backup/create       创建备份
- GET  /api/backup/list         列出备份
- GET  /api/backup/download/{f} 下载备份
- DELETE /api/backup/{f}        删除备份
- POST /api/backup/restore      上传备份包恢复（覆盖当前数据库，高风险）
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services.backup_service import (
    create_backup,
    delete_backup,
    get_backup_path,
    list_backups,
    restore_backup,
)

router = APIRouter(prefix="/api/backup", tags=["backup"])


class BackupCreateRequest(BaseModel):
    include_documents: bool = False


@router.get("/list")
def list_backup_files():
    return {"ok": True, "backups": list_backups()}


@router.post("/create")
def create_backup_endpoint(req: BackupCreateRequest):
    try:
        info = create_backup(include_documents=req.include_documents)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"备份失败: {e}") from e
    return {"ok": True, **info}


@router.get("/download/{filename}")
def download_backup(filename: str):
    path = get_backup_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="备份不存在")
    return FileResponse(
        str(path),
        media_type="application/zip",
        filename=path.name,
    )


@router.delete("/{filename}")
def delete_backup_endpoint(filename: str):
    if not delete_backup(filename):
        raise HTTPException(status_code=404, detail="备份不存在或文件名非法")
    return {"ok": True}


@router.post("/restore")
async def restore_backup_endpoint(file: UploadFile):
    """上传备份 zip 并恢复。会覆盖当前数据库，请谨慎操作。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 备份包")
    tmp = Path(file.filename or "restore.zip")
    # 写入临时文件
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(await file.read())
        tmp_path = Path(f.name)
    try:
        result = restore_backup(tmp_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"恢复失败: {e}") from e
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return {"ok": True, **result}
