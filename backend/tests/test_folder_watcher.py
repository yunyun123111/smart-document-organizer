"""目录自动监控服务测试（Phase 1：folder_watcher）。

覆盖：新文件触发 / 无变化不触发 / 非法目录拒绝 / 任务运行中不叠加 /
目录损坏恢复 / 配置保存后线程动态启停。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from backend.config import settings
from backend.database import SessionLocal
from backend.models import JOB_CANCELLED, JOB_COMPLETED, ProcessingJob
from backend.services import folder_watcher
from backend.services.processing_service import processing_service


@pytest.fixture
def watch_dir(tmp_path) -> Path:
    d = tmp_path / "watch"
    d.mkdir()
    return d


def _reset_watch_state():
    folder_watcher._snapshots.clear()
    with folder_watcher._status_lock:
        folder_watcher._status["dirs"] = {}
        folder_watcher._status["last_scan_at"] = None


def _set_watch_config(enabled: bool, dirs: list, interval: int = 3600):
    """写入监控配置（不改 .env，仅内存生效）。"""
    settings.update(
        WATCH_ENABLED=enabled,
        WATCH_DIRS=json.dumps([str(d) for d in dirs], ensure_ascii=False),
        WATCH_INTERVAL=interval,
    )


def _wait_job(job_id: int, statuses: set[str], timeout: float = 30.0) -> ProcessingJob:
    """轮询任务直至落入指定终态（避免后台线程在用例 teardown 时写库）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job and job.status in statuses:
                db.expunge(job)
                return job
        time.sleep(0.1)
    raise TimeoutError(f"job #{job_id} 未在超时内进入 {statuses}")


def _rmtree_retry(d: Path, attempts: int = 5):
    """Windows 下文件句柄释放有延迟，删除失败重试。"""
    for i in range(attempts):
        try:
            shutil.rmtree(d)
            return
        except PermissionError:
            time.sleep(0.4)
    raise PermissionError(f"删除目录失败（重试 {attempts} 次）: {d}")


# ---- 1. 新文件触发 ----
def test_scan_creates_job_for_new_file(watch_dir):
    _reset_watch_state()
    (watch_dir / "a.txt").write_text("销售合同 合同编号 XS202608001", encoding="utf-8")

    job_id = folder_watcher.scan_dir(watch_dir)
    assert job_id > 0
    job = _wait_job(job_id, {JOB_COMPLETED, JOB_CANCELLED})
    assert job.status == JOB_COMPLETED
    assert job.total_files == 1  # start_job 已把新文件计入任务


# ---- 2. 无变化不触发 ----
def test_scan_skips_when_no_change(watch_dir, monkeypatch):
    _reset_watch_state()
    (watch_dir / "a.txt").write_text("销售合同", encoding="utf-8")
    calls: list = []

    def fake_start_job(source_dir=None):
        calls.append(source_dir)
        return ProcessingJob(id=1, source_dir=str(source_dir), status="pending")

    monkeypatch.setattr(processing_service, "start_job", fake_start_job)
    monkeypatch.setattr(processing_service, "has_running", lambda: False)

    folder_watcher.scan_dir(watch_dir)
    assert len(calls) == 1  # 首次发现新文件触发

    folder_watcher.scan_dir(watch_dir)
    assert len(calls) == 1  # 无变化，不再触发


# ---- 3. 非法目录（归档目录 / 回收站）拒绝 ----
def test_scan_rejects_archive_and_recycle_dirs():
    _reset_watch_state()
    # 归档目录本身
    assert folder_watcher.scan_dir(settings.document_root) == 0
    # 回收站本身
    assert folder_watcher.scan_dir(settings.recycle_bin_root) == 0
    status = folder_watcher.get_status()
    assert status["dirs"], "应记录目录状态"
    for d in status["dirs"]:
        assert d["last_error"], "非法目录应有错误提示"


# ---- 4. 运行中任务不叠加 ----
def test_scan_skips_when_job_running(watch_dir, monkeypatch):
    _reset_watch_state()
    (watch_dir / "a.txt").write_text("销售合同", encoding="utf-8")
    calls: list = []

    def fake_start_job(source_dir=None):
        calls.append(source_dir)
        return ProcessingJob(id=1, source_dir=str(source_dir), status="running")

    monkeypatch.setattr(processing_service, "start_job", fake_start_job)
    monkeypatch.setattr(processing_service, "has_running", lambda: True)

    folder_watcher.scan_dir(watch_dir)
    assert calls == []  # 有运行中任务时不叠加触发
    status = folder_watcher.get_status()
    assert status["dirs"][0]["found_files"] > 0  # 仍记录发现了新文件


# ---- 5. 目录损坏 → 报错不崩溃 → 重建后恢复 ----
def test_scan_survives_missing_dir_and_recovers(tmp_path):
    _reset_watch_state()
    d = tmp_path / "gone"
    d.mkdir()

    # 正常扫描一次
    (d / "a.txt").write_text("销售合同", encoding="utf-8")
    job_id = folder_watcher.scan_dir(d)
    assert job_id > 0
    _wait_job(job_id, {JOB_COMPLETED, JOB_CANCELLED})

    # 目录被删除：不崩溃、记录错误
    _rmtree_retry(d)
    assert folder_watcher.scan_dir(d) == 0
    status = folder_watcher.get_status()
    err = [x for x in status["dirs"] if x["dir"] == str(d.resolve())]
    assert err and err[0]["last_error"], "目录删除后应有错误提示"

    # 重建目录：恢复正常扫描
    d.mkdir()
    (d / "b.txt").write_text("销售合同", encoding="utf-8")
    job_id = folder_watcher.scan_dir(d)
    assert job_id > 0
    _wait_job(job_id, {JOB_COMPLETED, JOB_CANCELLED})


# ---- 6. 配置保存 → 线程动态启停 ----
def test_sync_from_settings_start_stop(watch_dir):
    folder_watcher.stop_watching()
    try:
        _set_watch_config(True, [watch_dir])
        assert folder_watcher.sync_from_settings() is True
        assert folder_watcher.get_status()["running"] is True

        _set_watch_config(False, [watch_dir])
        assert folder_watcher.sync_from_settings() is False
        assert folder_watcher.get_status()["running"] is False
    finally:
        folder_watcher.stop_watching()
        _set_watch_config(False, [])


def test_watch_dirs_parses_json_list(watch_dir):
    """WATCH_DIRS JSON 数组字符串解析为目录列表。"""
    _set_watch_config(True, [watch_dir])
    dirs = settings.watch_dirs
    assert isinstance(dirs, list)
    assert len(dirs) == 1
    assert dirs[0].resolve() == watch_dir.resolve()
    _set_watch_config(False, [])
