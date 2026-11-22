# -*- coding: utf-8 -*-
"""桌面版版本更新：查清单、下载安装包、静默升级。

更新包来源：GitHub releases 分支 desktop.json + SmartDocOrganizer-Setup-*.exe。
源码运行（未打包）不做自动更新，避免覆盖开发环境。
"""
from __future__ import annotations

import os
import sys
import threading
import time

from backend.config import DATA_HOME

_lock = threading.Lock()
_state: dict = {
    "phase": "idle",  # idle | downloading | launching | error
    "progress": 0.0,
    "message": "",
    "error": "",
    "updated_at": "",
}
_cache: dict = {"at": 0.0, "data": None}
_CACHE_TTL = 300


def _updater():
    """desktop_update 位于项目根，打包后由 spec 的 hiddenimports 带入。"""
    try:
        import desktop_update

        return desktop_update
    except Exception:
        return None


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)
        _state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def get_state() -> dict:
    with _lock:
        return dict(_state)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_build() -> str:
    mod = _updater()
    return mod.current_build() if mod else ""


def info(force: bool = False) -> dict:
    """返回当前版本与远端最新版本。非打包形态只报本地信息。"""
    frozen = is_frozen()
    build = current_build()
    if not frozen:
        return {
            "supported": False,
            "current_build": build,
            "current_version": "",
            "latest": None,
            "has_update": False,
            "message": "源码运行模式不做自动更新，请用 git pull 或 sync.ps1 同步",
        }

    mod = _updater()
    if not mod:
        return {
            "supported": False,
            "current_build": build,
            "latest": None,
            "has_update": False,
            "message": "更新模块缺失（安装包不完整）",
        }

    if force or time.time() - _cache["at"] > _CACHE_TTL:
        _cache["data"] = mod.load_manifest()
        _cache["at"] = time.time()
    manifest = _cache["data"]

    if not manifest:
        return {
            "supported": True,
            "current_build": build,
            "latest": None,
            "has_update": False,
            "message": "暂时读不到更新清单，可能是网络问题，稍后重试",
        }

    return {
        "supported": True,
        "current_build": build,
        "current_version": manifest.get("version", ""),
        "latest": {
            "build": manifest.get("build", ""),
            "version": manifest.get("version", ""),
            "size": int(manifest.get("setup_size") or 0),
            "notes": manifest.get("notes", ""),
        },
        "has_update": mod.is_newer(str(manifest.get("build", "")), build),
        "message": "",
    }


def _run_update(mod) -> None:
    dest = DATA_HOME / "temp" / "update"
    last = [0.0]

    def on_progress(done: int, total: int) -> None:
        if time.time() - last[0] < 0.4:
            return
        last[0] = time.time()
        pct = (done / total * 100) if total else 0.0
        _set(phase="downloading", progress=round(min(pct, 99.0), 1),
             message=f"下载中 {done / 1048576:.1f} / {total / 1048576:.1f} MB")

    try:
        manifest = mod.load_manifest()
        if not manifest:
            raise RuntimeError("读取更新清单失败")
        _set(phase="downloading", progress=0.0, message="连接下载服务器...", error="")
        setup = mod.download(manifest, dest, log=lambda m: None, progress=on_progress)
        if not setup:
            raise RuntimeError("安装包下载或校验失败，请检查网络后重试")
        _set(phase="launching", progress=100.0, message="下载完成，正在启动安装程序...")
        if not mod.launch_installer(setup):
            raise RuntimeError("无法启动安装程序")
        # 安装器会关闭占用文件的进程并替换文件；这里主动退出让升级顺利进行
        time.sleep(1.5)
        os._exit(0)
    except Exception as exc:
        _set(phase="error", progress=0.0, message="更新失败", error=str(exc))


def start_update() -> dict:
    if not is_frozen():
        return {"started": False, "reason": "源码运行模式不做自动更新"}
    mod = _updater()
    if not mod:
        return {"started": False, "reason": "更新模块缺失"}
    if get_state()["phase"] == "downloading":
        return {"started": False, "reason": "已有下载任务在进行"}
    _set(phase="downloading", progress=0.0, message="准备下载...", error="")
    threading.Thread(target=_run_update, args=(mod,), daemon=True).start()
    return {"started": True}
