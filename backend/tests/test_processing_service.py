"""批量整理任务状态流转回归测试。

覆盖核心链路（防"修好又复发"）：
- 空收件箱：任务立即完成（对应前端"开始按钮转圈"bug 的后端侧）
- 文件名规则命中：直接自动归档（success）
- 内容置信度不足：进入人工审核（review）
- 单文件异常：标记 failed，不产生僵尸 pending/processing 状态
- 取消任务 / 取消未知任务
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import (
    SessionLocal,
    seed_default_categories,
    seed_default_filename_rules,
    seed_default_rename_templates,
    seed_default_rules,
)
from backend.models import (
    STATUS_ARCHIVED,
    STATUS_FAILED,
    STATUS_NEED_REVIEW,
    Document,
    JOB_CANCELLED,
    JOB_COMPLETED,
    ProcessingJob,
)
from backend.services.classifier import ClassifierService
from backend.services.processing_service import processing_service

# 与 test_api.py 相同的销售合同样例文本（内容命中规则但置信度 ~0.8 → 待审核）
CONTRACT_TXT = (
    "销售合同\n"
    "合同编号：XS202608001\n"
    "甲方：ABC有限公司\n"
    "乙方：DEF有限公司\n"
    "合同金额：人民币 128,500.00 元\n"
    "签订日期：2026年08月20日\n"
    "本合同由甲乙双方根据《中华人民共和国民法典》订立。\n"
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离的 inbox / 归档目录 + 默认种子数据。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_default_categories(db)
        seed_default_rules(db)
        seed_default_filename_rules(db)
        seed_default_rename_templates(db)
    return tmp_path


def _wait_job(job_id: int, statuses: set[str], timeout: float = 30.0) -> ProcessingJob:
    """轮询任务直至落入指定终态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job and job.status in statuses:
                return job
        time.sleep(0.1)
    raise TimeoutError(f"job #{job_id} 未在超时内进入 {statuses}")


def _job_from_db(job_id: int) -> ProcessingJob:
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        db.expunge(job)
        return job


def _docs() -> list[Document]:
    with SessionLocal() as db:
        return list(db.query(Document).all())


class TestEmptyInbox:
    def test_empty_inbox_completes_immediately(self, env):
        """空收件箱：start_job 返回后任务立即为 completed。

        回归：前端"开始整理"后收件箱为 0 时，任务会瞬间完成，
        若后端不立即落终态，前端轮询不到 completed 就会一直转圈。
        """
        job = processing_service.start_job()
        assert job.status == "running"  # 返回的内存对象仍是 running（历史行为）
        db_job = _job_from_db(job.id)
        assert db_job.status == JOB_COMPLETED  # 数据库已同步为终态
        assert db_job.finished_at is not None
        assert db_job.total_files == 0
        assert db_job.processed_files == 0

    def test_cancel_unknown_job_returns_false(self, env):
        assert processing_service.cancel_job(99999) is False


class TestProcessing:
    def test_filename_rule_auto_archive(self, env):
        """文件名命中规则（SJWLXS 前缀=销售合同）→ 直接自动归档。"""
        src = Path(settings.inbox_root) / "SJWLXS（DD）-2026-YC0001.txt"
        src.write_text(CONTRACT_TXT, encoding="utf-8")

        job = processing_service.start_job()
        done = _wait_job(job.id, {JOB_COMPLETED})
        assert done.success_count == 1
        assert done.review_count == 0
        assert done.failed_count == 0
        assert done.processed_files == 1

        docs = _docs()
        assert len(docs) == 1
        assert docs[0].status == STATUS_ARCHIVED
        assert "销售合同" in docs[0].current_path  # 归档到 合同/销售合同 子目录
        assert Path(docs[0].current_path).exists()

    def test_content_low_confidence_goes_review(self, env):
        """内容命中但置信度不足 → 待人工审核。"""
        src = Path(settings.inbox_root) / "销售合同.txt"
        src.write_text(CONTRACT_TXT, encoding="utf-8")

        job = processing_service.start_job()
        done = _wait_job(job.id, {JOB_COMPLETED})
        assert done.review_count == 1
        assert done.success_count == 0

        docs = _docs()
        assert len(docs) == 1
        assert docs[0].status == STATUS_NEED_REVIEW

    def test_unknown_text_goes_review(self, env):
        """无法识别的内容 → 进入人工审核而非丢文件。"""
        src = Path(settings.inbox_root) / "随笔.txt"
        src.write_text("今天天气不错，出门散步。", encoding="utf-8")

        job = processing_service.start_job()
        done = _wait_job(job.id, {JOB_COMPLETED})
        assert done.review_count == 1
        assert done.failed_count == 0

    def test_exception_marks_document_failed(self, env, monkeypatch):
        """识别抛异常 → 文档标记 failed，任务 failed 计数 +1，无僵尸状态。"""
        src = Path(settings.inbox_root) / "坏文件.txt"
        src.write_text(CONTRACT_TXT, encoding="utf-8")

        def boom(self_, *a, **k):
            raise RuntimeError("模拟识别故障")

        monkeypatch.setattr(ClassifierService, "analyze", boom)
        job = processing_service.start_job()
        done = _wait_job(job.id, {JOB_COMPLETED})
        assert done.failed_count == 1
        assert done.processed_files == 1

        docs = _docs()
        assert len(docs) == 1
        assert docs[0].status == STATUS_FAILED  # 不是 pending/processing 僵尸

    def test_cancel_running_job(self, env, monkeypatch):
        """处理中取消 → 任务最终 cancelled。"""
        src = Path(settings.inbox_root) / "慢文件.txt"
        src.write_text(CONTRACT_TXT, encoding="utf-8")

        # 让每个文件处理耗时 3s，给取消留出窗口
        orig_analyze = ClassifierService.analyze

        def slow(self_, *a, **k):
            time.sleep(3)
            return orig_analyze(self_, *a, **k)

        monkeypatch.setattr(ClassifierService, "analyze", slow)

        job = processing_service.start_job()
        assert processing_service.cancel_job(job.id) is True
        done = _wait_job(job.id, {JOB_CANCELLED, JOB_COMPLETED})
        assert done.status == JOB_CANCELLED
