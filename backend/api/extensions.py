"""功能扩展包 API：查看状态、一键安装、进度轮询、卸载。

主安装包不含 OCR / OpenCV，用户在设置页点「一键下载」即可补齐。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.services import extension_service as ext

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


@router.get("")
def list_extensions():
    """所有扩展包的安装状态与远端版本。"""
    return ext.status()


@router.get("/{name}/progress")
def get_progress(name: str):
    """轮询安装进度（前端每 800ms 一次）。"""
    if name not in ext.PACKAGES:
        raise HTTPException(status_code=404, detail=f"未知扩展包：{name}")
    return {"name": name, "state": ext.get_state(name)}


@router.post("/{name}/install")
def install(name: str):
    """启动后台下载安装，立即返回；进度用 /progress 轮询。"""
    if name not in ext.PACKAGES:
        raise HTTPException(status_code=404, detail=f"未知扩展包：{name}")
    return ext.install(name)


@router.post("/{name}/uninstall")
def uninstall(name: str):
    """卸载扩展包；文件被占用时会在下次启动时删除。"""
    if name not in ext.PACKAGES:
        raise HTTPException(status_code=404, detail=f"未知扩展包：{name}")
    return ext.uninstall(name)
