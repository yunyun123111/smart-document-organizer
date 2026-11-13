"""对账定时巡检（P2）。

每天按 cron 自动跑一次对账，并把「本轮新出现差异 / 差异数增加」的业务线
记进状态文件，页面据此显示变化提醒。

设计：
- 后台守护线程，模式与 folder_watcher 一致（同步 settings 启停）；
- 配置存 data/reconciliation_schedule.json，重启后保留；
- 只支持「分 时 * * *」这种每天固定时刻的写法（本项目的实际用法），
  其它 cron 字段会被拒绝，避免配了个不生效的表达式还静默成功。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.database import SessionLocal
from backend.utils.logger import get_logger

logger = get_logger("services.reconciliation_scheduler")

_DEFAULT_CRON = "0 8 * * *"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None

_state: dict = {
    "enabled": False,
    "cron": _DEFAULT_CRON,
    "last_run_at": "",
    "last_result": "",
    "changed_businesses": [],
    "last_error": "",
}


def _state_file() -> Path:
    # data/ 目录：与日志同级的 data 下，不新建数据目录树
    return Path(settings.LOG_DIR).parent / "reconciliation_schedule.json"


def _load() -> None:
    path = _state_file()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("定时巡检配置读取失败，使用默认值")
        return
    if isinstance(data, dict):
        for key in ("enabled", "cron", "last_run_at", "last_result", "changed_businesses", "last_error"):
            if key in data:
                _state[key] = data[key]


def _save() -> None:
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("定时巡检配置保存失败（内存状态仍然生效）")


def _parse_cron(cron: str) -> tuple[int, int]:
    """解析「分 时 * * *」，返回 (minute, hour)。不支持的写法抛 ValueError。"""
    parts = (cron or "").split()
    if len(parts) != 5:
        raise ValueError("cron 需要 5 段，例如 0 8 * * *")
    if parts[2:] != ["*", "*", "*"]:
        raise ValueError("目前只支持「分 时 * * *」这种每天固定时刻的写法")
    if not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError("目前只支持「分 时 * * *」这种每天固定时刻的写法（不支持 */n、区间、列表）")
    minute, hour = int(parts[0]), int(parts[1])
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        raise ValueError("分钟需在 0-59、小时需在 0-23")
    return minute, hour


def get_schedule() -> dict:
    with _lock:
        return dict(_state)


def set_schedule(enabled: bool | None = None, cron: str | None = None) -> dict:
    with _lock:
        if cron is not None:
            # 表达式不合法时直接抛出，不写进状态
            _parse_cron(cron)
            _state["cron"] = cron.strip()
        if enabled is not None:
            _state["enabled"] = bool(enabled)
        _save()
    sync_thread()
    return get_schedule()


def _snapshot() -> dict[str, tuple[str, int]]:
    """当前报告快照：业务线 → (状态, 差异数)。"""
    from backend.models import ReconciliationReport
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = db.execute(select(ReconciliationReport.business_no, ReconciliationReport.status,
                                ReconciliationReport.diff_count)).all()
    return {row[0]: (row[1], int(row[2] or 0)) for row in rows}


def run_once(reason: str = "schedule") -> dict:
    """跑一次全量对账并对比变化。返回本轮结果摘要。"""
    before = _snapshot()
    try:
        from backend.services.reconciliation_service import ReconciliationService

        with SessionLocal() as db:
            result = ReconciliationService(db).run()
    except Exception as exc:  # 台账没配、文件被占用等，不静默
        logger.exception("定时对账失败")
        with _lock:
            _state["last_error"] = f"{datetime.now():%Y-%m-%d %H:%M:%S} {exc}"
            _save()
        return {"ok": False, "error": str(exc)}

    after = _snapshot()
    changed: list[str] = []
    for business_no, (status, count) in after.items():
        old = before.get(business_no)
        if old is None:
            changed.append(business_no)          # 新业务线
            continue
        old_status, old_count = old
        if count > old_count or status != old_status:
            changed.append(business_no)          # 差异变多或状态变差/变好

    summary = {
        "ok": True,
        "total": len(after),
        "changed": changed,
        "reason": reason,
    }
    with _lock:
        _state["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _state["last_result"] = (
            f"共 {result.get('total', len(after))} 条："
            f"已对齐 {result.get('ok', 0)} / 警告 {result.get('warning', 0)} / "
            f"错误 {result.get('error', 0)} / 未上传资料 {result.get('no_data', 0)}"
        )
        _state["changed_businesses"] = changed
        _state["last_error"] = ""
        _save()
    logger.info("定时对账完成：%s，变化 %d 条", _state["last_result"], len(changed))
    return summary


def _loop() -> None:
    from datetime import timedelta

    last_day_minute = ""
    while _stop_event is not None and not _stop_event.is_set():
        schedule = get_schedule()
        if schedule.get("enabled"):
            try:
                minute, hour = _parse_cron(schedule["cron"])
                now = datetime.now()
                stamp = now.strftime("%Y-%m-%d %H:%M")
                if now.hour == hour and now.minute == minute and stamp != last_day_minute:
                    last_day_minute = stamp
                    run_once(reason="schedule")
            except Exception:  # noqa: BLE001
                logger.exception("定时巡检轮询异常")
        if _stop_event:
            _stop_event.wait(30)


def start() -> bool:
    global _thread, _stop_event
    if _thread and _thread.is_alive():
        return True
    _stop_event = threading.Event()
    _thread = threading.Thread(target=_loop, name="recon-scheduler", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    global _thread, _stop_event
    if _stop_event:
        _stop_event.set()
    _thread = None
    _stop_event = None


def sync_thread() -> bool:
    """开关变化后调整线程：启用则起，停用则停。"""
    if get_schedule().get("enabled"):
        return start()
    stop()
    return False


def clear_changes() -> None:
    """页面看过提醒后清空变化列表。"""
    with _lock:
        _state["changed_businesses"] = []
        _save()


# 模块导入即恢复上次配置（只读文件，线程由 sync_thread 决定）
_load()
