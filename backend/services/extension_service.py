# -*- coding: utf-8 -*-
"""扩展包管理：按需下载、校验、解压、卸载。

主安装包为了体积剥离了 OCR（rapidocr / onnxruntime）与 OpenCV。
用户可在「设置 → 功能扩展」里一键下载 vision 扩展包（约 52 MB），
解压到 %APPDATA%\\SmartDocOrganizer\\extensions\\vision，重启后由
backend.config 在导入任何业务模块之前挂到 sys.path。

清单文件放在 GitHub releases 分支的 extensions/vision.json：
    {"package":"vision","version":"20261122.150000","file":"vision.zip",
     "size":54600000,"sha256":"...","contains":[...]}
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from backend.config import ACTIVATED_EXTENSIONS, DATA_HOME, EXTENSIONS_DIR

REPO = "yunyun123111/smart-document-organizer"
BRANCH = "releases"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
MIRRORS = ("https://ghfast.top/", "https://gh-proxy.com/", "")
UA = "SmartDocExtensions/1.0"

PACKAGES: dict[str, dict] = {
    "vision": {
        "name": "vision",
        "title": "OCR 文字识别 与 盖章检测",
        "summary": "扫描件/图片转文字（RapidOCR），以及图片、PDF 首页的红色印章检测（OpenCV）。",
        "detail": "不安装也能正常整理 Word、Excel、文本型 PDF；只有扫描件识别和盖章检测需要它。",
        "manifest_path": "extensions/vision.json",
        "zip_name": "vision.zip",
        "size_hint": 52 * 1024 * 1024,
    }
}

_TMP_DIR = DATA_HOME / "temp"
_MANIFEST_CACHE: dict[str, tuple[float, dict | None]] = {}
_MANIFEST_TTL = 300

_lock = threading.Lock()
_state: dict[str, dict] = {}
_PROBE: dict | None = None


def _probe_runtime() -> dict:
    """当前进程里 OCR / 盖章检测是否真的可用（结果缓存一次）。

    扩展包装好但没重启时，这里仍是 False —— 设置页据此提示"重启后生效"。
    """
    global _PROBE
    if _PROBE is not None:
        return _PROBE
    result = {"ocr": False, "seal": False}
    try:
        import rapidocr_onnxruntime  # noqa: F401

        result["ocr"] = True
    except Exception:
        pass
    try:
        import cv2  # noqa: F401

        result["seal"] = True
    except Exception:
        pass
    _PROBE = result
    return result


# --------------------------------------------------------------------------- 工具

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ext_dir(name: str) -> Path:
    return EXTENSIONS_DIR / name


def _meta_path(name: str) -> Path:
    return _ext_dir(name) / ".installed.json"


def _read_meta(name: str) -> dict:
    try:
        return json.loads(_meta_path(name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _set_state(name: str, **kw) -> None:
    with _lock:
        cur = _state.setdefault(
            name, {"phase": "idle", "progress": 0.0, "message": "", "error": "", "updated_at": _now()}
        )
        cur.update(kw)
        cur["updated_at"] = _now()


def get_state(name: str) -> dict:
    with _lock:
        return dict(_state.get(name, {"phase": "idle", "progress": 0.0, "message": "", "error": "", "updated_at": _now()}))


def _busy(name: str) -> bool:
    return get_state(name).get("phase") in ("downloading", "installing")


# --------------------------------------------------------------------------- 远端清单

def remote_manifest(name: str = "vision", force: bool = False) -> dict | None:
    """读取远端扩展包清单；带 5 分钟缓存，失败返回 None。"""
    cached = _MANIFEST_CACHE.get(name)
    if not force and cached and time.time() - cached[0] < _MANIFEST_TTL:
        return cached[1]
    spec = PACKAGES[name]
    stamp = int(time.time())
    url = f"{RAW_BASE}/{spec['manifest_path']}?t={stamp}"
    data: dict | None = None
    for mirror in MIRRORS:
        try:
            req = urllib.request.Request(
                f"{mirror}{url}" if mirror else url,
                headers={"User-Agent": UA, "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            if isinstance(obj, dict) and (obj.get("sha256") or obj.get("file")):
                data = obj
                break
        except Exception:
            continue
    _MANIFEST_CACHE[name] = (time.time(), data)
    return data


# --------------------------------------------------------------------------- 状态查询

def status() -> dict:
    """返回所有扩展包的安装状态与远端版本，供设置页渲染。"""
    items = []
    for name, spec in PACKAGES.items():
        path = _ext_dir(name)
        meta = _read_meta(name)
        installed = path.is_dir() and any(path.iterdir())
        remote = remote_manifest(name)
        pending_delete = (path.parent / f".{name}.pending-delete").exists()
        items.append(
            {
                "name": name,
                "title": spec["title"],
                "summary": spec["summary"],
                "detail": spec["detail"],
                "installed": installed,
                "active": installed and (name in ACTIVATED_EXTENSIONS or not installed),
                "version": meta.get("version", ""),
                "installed_at": meta.get("installed_at", ""),
                "size": _dir_size(path) if installed else 0,
                "size_hint": spec["size_hint"],
                "remote_version": (remote or {}).get("version", ""),
                "remote_size": int((remote or {}).get("size") or 0),
                "update_available": bool(
                    installed and (remote or {}).get("version") and (remote or {}).get("version") != meta.get("version")
                ),
                "remote_ok": bool(remote),
                "pending_delete": pending_delete,
                "state": get_state(name),
                "runtime": _probe_runtime() if installed else {"ocr": False, "seal": False},
                "requires_restart": bool(installed and name not in ACTIVATED_EXTENSIONS),
            }
        )
    return {"items": items, "extensions_dir": str(EXTENSIONS_DIR), "data_home": str(DATA_HOME)}


# --------------------------------------------------------------------------- 下载与安装

def _download_to(url: str, dest: Path, expected_size: int, expected_sha: str, name: str) -> bool:
    """流式下载并校验大小与 SHA256。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0) or expected_size
        done = 0
        last = 0.0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = min(88.0, done * 88.0 / total)
                if time.time() - last > 0.3:
                    last = time.time()
                    _set_state(
                        name,
                        phase="downloading",
                        progress=round(pct, 1),
                        message=f"下载中 {done / 1048576:.1f} / {total / 1048576:.1f} MB",
                    )
    actual_size = dest.stat().st_size
    if expected_size and actual_size != expected_size:
        raise ValueError(f"文件大小不符：{actual_size} != {expected_size}")
    if expected_sha and _sha256(dest).lower() != expected_sha.lower():
        raise ValueError("SHA256 校验失败，文件可能在传输中损坏")
    return True


def _extract(zip_path: Path, target: Path) -> None:
    """解压到 target（先清空），并阻止路径穿越。"""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            parts = Path(member.filename).parts
            if member.filename.startswith("/") or ".." in parts:
                raise ValueError(f"压缩包含非法路径：{member.filename}")
        z.extractall(target)


def _run_install(name: str) -> None:
    spec = PACKAGES[name]
    tmp_zip = _TMP_DIR / f"{name}-download.zip"
    staging = EXTENSIONS_DIR / f".{name}.new"
    try:
        manifest = remote_manifest(name, force=True)
        if not manifest:
            raise RuntimeError("无法读取扩展包清单，请检查网络后重试")

        file_name = Path(str(manifest.get("file") or spec["zip_name"])).name
        sha = str(manifest.get("sha256") or "").lower()
        size = int(manifest.get("size") or 0)

        _set_state(name, phase="downloading", progress=0.0, message="连接下载服务器...", error="")
        ok = False
        last_err = ""
        for mirror in MIRRORS:
            url = f"{mirror}{RAW_BASE}/extensions/{file_name}" if mirror else f"{RAW_BASE}/extensions/{file_name}"
            try:
                _download_to(url, tmp_zip, size, sha, name)
                ok = True
                break
            except Exception as exc:  # 换镜像重试
                last_err = str(exc)
                _set_state(name, message=f"镜像失败，切换下一个（{last_err[:60]}）")
                tmp_zip.unlink(missing_ok=True)
                continue
        if not ok:
            raise RuntimeError(f"所有下载源均失败：{last_err}")

        _set_state(name, phase="installing", progress=90.0, message="校验通过，正在解压...")
        _extract(tmp_zip, staging)

        # 替换旧版本：先把旧的挪走，成功后删除
        target = _ext_dir(name)
        old = EXTENSIONS_DIR / f".{name}.old"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            try:
                target.rename(old)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
        shutil.rmtree(old, ignore_errors=True)

        (target / ".installed.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "version": manifest.get("version", ""),
                    "size": _dir_size(target),
                    "sha256": sha,
                    "contains": manifest.get("contains", []),
                    "installed_at": _now(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        _set_state(
            name,
            phase="done",
            progress=100.0,
            message="安装完成，重启应用后生效",
            error="",
        )
    except Exception as exc:
        _set_state(name, phase="error", message="安装失败", error=str(exc))
        shutil.rmtree(staging, ignore_errors=True)
    finally:
        tmp_zip.unlink(missing_ok=True)
        _MANIFEST_CACHE.pop(name, None)


def install(name: str = "vision") -> dict:
    """后台线程安装，立即返回；进度用 get_state 轮询。"""
    if name not in PACKAGES:
        raise KeyError(f"未知扩展包：{name}")
    if _busy(name):
        return {"started": False, "reason": "已有安装任务在进行"}
    _set_state(name, phase="downloading", progress=0.0, message="准备下载...", error="")
    threading.Thread(target=_run_install, args=(name,), daemon=True).start()
    return {"started": True}


def uninstall(name: str = "vision") -> dict:
    """删除扩展包。进程已加载的 DLL 在 Windows 上无法删除，改为标记待删。"""
    target = _ext_dir(name)
    if not target.exists():
        return {"removed": False, "reason": "未安装"}
    marker = EXTENSIONS_DIR / f".{name}.pending-delete"
    try:
        shutil.rmtree(target)
    except Exception:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_now(), encoding="utf-8")
        return {"removed": False, "pending": True, "reason": "文件被占用，重启后自动删除"}
    marker.unlink(missing_ok=True)
    _set_state(name, phase="idle", progress=0.0, message="已卸载", error="")
    return {"removed": True, "pending": False}


def apply_pending_deletions() -> None:
    """启动时清理上次因文件占用没删掉的扩展包。"""
    if not EXTENSIONS_DIR.is_dir():
        return
    for marker in EXTENSIONS_DIR.glob(".*.pending-delete"):
        name = marker.name[1 : -len(".pending-delete")]
        shutil.rmtree(_ext_dir(name), ignore_errors=True)
        marker.unlink(missing_ok=True)
    for junk in list(EXTENSIONS_DIR.glob(".*.new")) + list(EXTENSIONS_DIR.glob(".*.old")):
        if junk.is_dir():
            shutil.rmtree(junk, ignore_errors=True)
