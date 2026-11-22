# -*- coding: utf-8 -*-
"""桌面版自动更新。

数据源：GitHub 仓库 releases 分支上的 desktop.json
    {
      "version": "1.0.0",
      "build": "20261122.140000",
      "setup_file": "SmartDocOrganizer-Setup-1.0.0.exe",
      "setup_sha256": "...",
      "setup_size": 97234123,
      "notes": "..."
    }

流程：读清单 -> 比较 build -> 下载安装包 -> 校验 SHA256 -> 静默启动安装器。
安装器（Inno Setup）会自动结束占用文件的进程、替换文件并重新启动新版本。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = "yunyun123111/smart-document-organizer"
BRANCH = "releases"
MANIFEST_NAME = "desktop.json"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
MANIFEST_URL = f"{RAW_BASE}/{MANIFEST_NAME}"

# 国内直连 raw.githubusercontent 常被重置，按可用性排序
MIRRORS = ("https://ghfast.top/", "https://gh-proxy.com/", "")

UA = "SmartDocUpdater/1.0"


def _url(url: str, mirror: str) -> str:
    return f"{mirror}{url}" if mirror else url


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_manifest() -> dict | None:
    """依次尝试各镜像读取远端清单，全部失败返回 None。"""
    stamp = int(time.time())
    for mirror in MIRRORS:
        url = _url(f"{MANIFEST_URL}?t={stamp}", mirror)
        try:
            data = _get_json(url)
            if isinstance(data, dict) and data.get("build"):
                return data
        except Exception:
            continue
    return None


def current_build() -> str:
    """打包时写入的构建号（yyyyMMdd.HHmmss）。源码运行返回空串。"""
    if not getattr(sys, "frozen", False):
        return ""
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    try:
        return (base / "build_version.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def is_newer(remote: str, local: str) -> bool:
    if not remote:
        return False
    if not local:
        return True
    return remote > local


def check() -> dict | None:
    """有可用更新时返回清单，否则返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    manifest = load_manifest()
    if not manifest:
        return None
    if not is_newer(str(manifest.get("build", "")), current_build()):
        return None
    return manifest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(manifest: dict, dest_dir: Path, log=print, progress=None) -> Path | None:
    """下载安装包并校验；成功返回文件路径，失败返回 None。

    progress(done_bytes, total_bytes) 可选，用于界面上报下载进度。
    """
    name = Path(str(manifest.get("setup_file") or "")).name
    if not name:
        log("[更新] 清单缺少 setup_file")
        return None
    expected = str(manifest.get("setup_sha256") or "").lower()
    size = int(manifest.get("setup_size") or 0)
    dest = dest_dir / name
    dest_dir.mkdir(parents=True, exist_ok=True)

    for mirror in MIRRORS:
        url = _url(f"{RAW_BASE}/{name}", mirror)
        try:
            log(f"[更新] 下载 {url}")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
                total = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    if progress:
                        try:
                            progress(total, size)
                        except Exception:
                            pass
            if size and total != size:
                log(f"[更新] 大小不符: {total} != {size}")
                dest.unlink(missing_ok=True)
                continue
            actual = _sha256(dest)
            if expected and actual != expected:
                log(f"[更新] 校验失败: {actual[:12]} != {expected[:12]}")
                dest.unlink(missing_ok=True)
                continue
            log(f"[更新] 下载完成 {total / 1048576:.1f} MB，校验通过")
            return dest
        except Exception as exc:
            log(f"[更新] 下载失败: {exc}")
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
    return None


def launch_installer(setup: Path) -> bool:
    """静默启动安装器。安装器负责关闭占用进程、替换文件、重启新版本。"""
    try:
        flags = 0x00000008  # DETACHED_PROCESS：父进程退出后继续运行
        subprocess.Popen(
            [str(setup), "/VERYSILENT", "/CLOSEAPPLICATIONS", "/NORESTART", "/SUPPRESSMSGBOXES"],
            close_fds=True,
            creationflags=flags,
        )
        return True
    except Exception:
        return False


def cleanup_old(dest_dir: Path, keep: Path | None = None) -> None:
    """清掉更新目录里的旧安装包，避免占空间。"""
    try:
        for p in dest_dir.glob("SmartDocOrganizer-Setup-*.exe"):
            if keep and p.resolve() == keep.resolve():
                continue
            p.unlink(missing_ok=True)
    except Exception:
        pass
