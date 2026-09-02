"""重复文件检测测试（P0-4）。

覆盖：
- 判重范围：已归档 / 人工审核中 / 已判重复 均判重；处理中不判（不误伤并发）
- 重复来源：返回最早一条
- API：status=duplicate 列表带 duplicate_of 来源
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.models import (
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_NEED_REVIEW,
    STATUS_PROCESSING,
    Document,
)
from backend.services.duplicate_service import duplicate_service


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


def _mk(db, filename: str, file_hash: str, status: str) -> Document:
    doc = Document(
        original_filename=filename,
        current_filename=filename,
        original_path=f"/p/{filename}",
        current_path=f"/p/{filename}",
        file_type="pdf",
        file_size=10,
        file_hash=file_hash,
        status=status,
    )
    db.add(doc)
    db.commit()
    return doc


class TestFindDuplicateScope:
    def test_match_archived(self, db):
        _mk(db, "a.pdf", "H1", STATUS_ARCHIVED)
        dup = duplicate_service.find_duplicate(db, "H1")
        assert dup is not None
        assert dup.original_filename == "a.pdf"

    def test_match_need_review(self, db):
        """核心：人工审核中的文件也判重，避免二次识别浪费 token。"""
        _mk(db, "a.pdf", "H1", STATUS_NEED_REVIEW)
        dup = duplicate_service.find_duplicate(db, "H1")
        assert dup is not None

    def test_match_duplicate(self, db):
        _mk(db, "a.pdf", "H1", STATUS_DUPLICATE)
        dup = duplicate_service.find_duplicate(db, "H1")
        assert dup is not None

    def test_no_match(self, db):
        _mk(db, "a.pdf", "H1", STATUS_ARCHIVED)
        assert duplicate_service.find_duplicate(db, "H2") is None

    def test_processing_not_matched(self, db):
        """处理中瞬态不判重，避免同批并发互相误伤。"""
        _mk(db, "a.pdf", "H1", STATUS_PROCESSING)
        assert duplicate_service.find_duplicate(db, "H1") is None

    def test_returns_earliest_as_source(self, db):
        _mk(db, "first.pdf", "H1", STATUS_ARCHIVED)
        _mk(db, "second.pdf", "H1", STATUS_NEED_REVIEW)
        dup = duplicate_service.find_duplicate(db, "H1")
        assert dup.original_filename == "first.pdf"


class TestDuplicateSourceApi:
    def test_list_duplicate_with_source(self, client, db):
        _mk(db, "原始合同.pdf", "HASH1", STATUS_ARCHIVED)
        _mk(db, "重复合同.pdf", "HASH1", STATUS_DUPLICATE)
        r = client.get("/api/documents?status=duplicate")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["status"] == "duplicate"
        assert rows[0]["duplicate_of"] == "原始合同.pdf"

    def test_archived_has_no_source(self, client, db):
        _mk(db, "a.pdf", "H", STATUS_ARCHIVED)
        r = client.get("/api/documents?status=archived")
        rows = r.json()
        assert rows[0]["status"] == "archived"
        assert rows[0]["duplicate_of"] is None

    def test_duplicate_no_original_has_no_source(self, client, db):
        """重复记录但原记录已被删除：来源为空但不报错。"""
        _mk(db, "dup.pdf", "H2", STATUS_DUPLICATE)
        r = client.get("/api/documents?status=duplicate")
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["duplicate_of"] is None
