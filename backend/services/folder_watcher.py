"""目录自动监控服务（主动式整理入口）。

- 用户配置一个或多个监控目录（WATCH_DIRS，JSON 数组）
- 后台线程按 WATCH_INTERVAL 轮询扫描，发现【新增/变化】文件
  → 调用 processing_service.start_job(dir) 自动触发整理
- 线程生命周期：start_watching / stop_watching / sync_from_settings，
  配置保存后动态启停（与 email_ingest 同模式，无需重启服务）
- 只"看"不"动"：本服务不移动/删除监控目录里的任何文件；
  文件归档时的移动仍走 ArchiveService 的既有安全流程。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.services.processing_service import processing_service
from backend.utils.file_utils import get_file_type
from backend.utils.fs_path import fs_exists, fs_isfile
from backend.utils.logger import get_logger

logger = get_logger("services.folder_watcher")

# ---- 全局状态（供界面展示） ----
_status_lock = threading.Lock()
_status: dict = {
    "last_scan_at": None,
    "dirs": {},  # dir -> {last_scan_at, found_files, last_job_id, last_error}
}
# 每个目录的上次文件快照：dir -> {(path, size, mtime)}
_snapshots: dict[str, set[tuple]] = {}

# ---- 轮询线程管理 ----
_watch_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None


def _fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def _is_valid_watch_dir(d: Path) -> tuple[bool, str]:
    """校验目录是否可作为监控目录（防循环：不能是归档目录/回收站）。"""
    if not d.is_absolute():
        return False, "必须使用绝对路径"
    if not fs_exists(d):
        return False, "目录不存在"
    if not d.is_dir():
        return False, "不是目录"
    # 归档目录 / 回收站 及其子目录都不能作为监控目录
    forbidden = [
        settings.document_root.resolve(),
        settings.recycle_bin_root.resolve(),
    ]
    dr = d.resolve()
    for f in forbidden:
        if dr == f or f in dr.parents:
            return False, f"不能监控归档目录/回收站目录: {f}"
    return True, ""


def _snapshot_dir(d: Path) -> set[tuple]:
    """生成目录内待处理文件快照：(path, size, mtime)。"""
    snap: set[tuple] = set()
    try:
        for p in d.iterdir():
            if not fs_isfile(p):
                continue
            if p.name.startswith((".", "~$")):
                continue
            try:
                if get_file_type(p) == "unknown":
                    continue
                st = p.stat()
                snap.add((str(p.resolve()), st.st_size, int(st.st_mtime)))
            except OSError:
                continue
    except OSError as e:
        logger.warning("扫描监控目录失败 %s: %s", d, e)
    return snap


def scan_dir(d: Path) -> int:
    """扫描单个监控目录，发现新文件则触发整理任务。返回触发任务号或 0。"""
    d = d.resolve()
    key = str(d)
    ok, err = _is_valid_watch_dir(d)
    if not ok:
        with _status_lock:
            _status["dirs"][key] = {
                "dir": key,
                "last_scan_at": _fmt(datetime.now()),
                "found_files": 0,
                "last_job_id": None,
                "last_error": err,
            }
        logger.warning("监控目录校验失败 %s: %s", d, err)
        return 0

    current = _snapshot_dir(d)
    with _status_lock:
        previous = _snapshots.get(key)
        _snapshots[key] = current

    # 只认"新增/变化"的文件；归档移走（上次有、这次无）不算新文件
    new_files = current - (previous or set())
    found = len(new_files)
    job_id: int | None = None
    last_error: str | None = None

    if found > 0:
        if processing_service.has_running():
            logger.info("监控目录 %s 发现 %d 个新文件，但已有任务运行，稍后自动处理", d, found)
        else:
            try:
                job = processing_service.start_job(str(d))
                job_id = job.id
                logger.info("监控目录 %s 触发整理任务 #%s（新文件 %d 个）", d, job_id, found)
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                logger.exception("监控目录 %s 触发整理失败: %s", d, e)
    else:
        logger.debug("监控目录 %s 无新文件", d)

    with _status_lock:
        _status["dirs"][key] = {
            "dir": key,
            "last_scan_at": _fmt(datetime.now()),
            "found_files": found,
            "last_job_id": job_id,
            "last_error": last_error,
        }
        _status["last_scan_at"] = _fmt(datetime.now())
    return job_id or 0


def _watch_loop(interval: int, stop_event: threading.Event) -> None:
    logger.info("目录监控线程开始 (interval=%ss, dirs=%s)", interval, settings.watch_dirs)
    try:
        while not stop_event.is_set():
            try:
                for d in settings.watch_dirs:
                    if stop_event.is_set():
                        break
                    scan_dir(d)
            except Exception:  # noqa: BLE001
                logger.exception("目录监控扫描异常")
            stop_event.wait(max(10, int(interval) or 30))
    finally:
        logger.info("目录监控线程停止")


def start_watching(interval: int | None = None) -> bool:
    """启动监控线程（若已在跑则忽略）。"""
    global _watch_thread, _stop_event
    if _watch_thread and _watch_thread.is_alive():
        return True
    _stop_event = threading.Event()
    _watch_thread = threading.Thread(
        target=_watch_loop,
        args=(interval or settings.WATCH_INTERVAL, _stop_event),
        name="folder-watcher",
        daemon=True,
    )
    _watch_thread.start()
    return True


def stop_watching() -> None:
    global _watch_thread, _stop_event
    if _stop_event:
        _stop_event.set()
    _watch_thread = None
    _stop_event = None


def sync_from_settings() -> bool:
    """根据当前 settings 启停监控。返回是否在运行。"""
    if settings.WATCH_ENABLED and settings.watch_dirs:
        start_watching()
        return True
    stop_watching()
    return False


def get_status() -> dict:
    with _status_lock:
        s = dict(_status)
        s["dirs"] = list(_status["dirs"].values())
    s["running"] = bool(_watch_thread and _watch_thread.is_alive())
    s["enabled"] = settings.WATCH_ENABLED
    s["interval"] = settings.WATCH_INTERVAL
    return s
