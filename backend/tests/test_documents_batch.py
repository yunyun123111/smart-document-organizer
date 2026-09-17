# -*- coding: utf-8 -*-
"""documents 批量端点测试：批量归档 / 批量重跑 / 批量撤销。"""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import settings
from backend.database import SessionLocal, seed_default_categories, seed_default_rules
from backend.main import app
from backend.models import (
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_PENDING,
    Document,
)

client = TestClient(app)


def _make_pending_doc(tmp_path: Path, name: str = "测试销售合同.pdf") -> tuple[Document, Path]:
    """在临时收件目录放一个文件并登记为 pending 文档。"""
    db = SessionLocal()
    try:
        seed_default_categories(db)
        seed_default_rules(db)
        src = tmp_path / "inbox" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"%PDF-1.4 fake content for tests" + name.encode("utf-8"))
        doc = Document(
            original_filename=name,
            current_filename=name,
            original_path=str(src),
            current_path=str(src),
            file_type="pdf",
            file_size=src.stat().st_size,
            file_hash="hash-" + name,
            status=STATUS_PENDING,
            document_type="",
            confidence=None,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc, src
    finally:
        db.close()


def test_batch_archive_pending(tmp_path, monkeypatch):
    """批量归档：pending 文档归档成功，状态 archived，文件移走。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)

    doc, src = _make_pending_doc(tmp_path)
    res = client.post("/api/documents/batch-archive", json={"doc_ids": [doc.id]})
    body = res.json()
    assert res.status_code == 200, body
    assert body["success_count"] == 1, body

    db = SessionLocal()
    try:
        d = db.get(Document, doc.id)
        assert d.status == STATUS_ARCHIVED
        assert Path(d.current_path).exists()
        assert not src.exists()  # 原位置已移动
    finally:
        db.close()


def test_batch_archive_with_category(tmp_path, monkeypatch):
    """批量归档到指定分类目录。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)

    doc, _ = _make_pending_doc(tmp_path)
    res = client.post(
        "/api/documents/batch-archive",
        json={"doc_ids": [doc.id], "category_path": "合同/销售合同"},
    )
    body = res.json()
    assert body["success_count"] == 1, body

    db = SessionLocal()
    try:
        d = db.get(Document, doc.id)
        assert d.status == STATUS_ARCHIVED
        assert "合同" in str(Path(d.current_path))
    finally:
        db.close()


def test_batch_archive_recycled_rejected(tmp_path, monkeypatch):
    """重复状态文档拒绝归档。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)

    doc, _ = _make_pending_doc(tmp_path)
    db = SessionLocal()
    try:
        d = db.get(Document, doc.id)
        d.status = STATUS_DUPLICATE
        db.commit()
    finally:
        db.close()

    res = client.post("/api/documents/batch-archive", json={"doc_ids": [doc.id]})
    body = res.json()
    assert body["success_count"] == 0
    assert body["failed_count"] == 1


def test_batch_undo_after_archive(tmp_path, monkeypatch):
    """归档后批量撤销：文件回到原位置，文档回到 pending。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)

    doc, src = _make_pending_doc(tmp_path)
    client.post("/api/documents/batch-archive", json={"doc_ids": [doc.id]})

    res = client.post("/api/documents/batch-undo", json={"doc_ids": [doc.id]})
    body = res.json()
    assert body["success_count"] == 1, body

    db = SessionLocal()
    try:
        d = db.get(Document, doc.id)
        assert d.status == STATUS_PENDING
        assert Path(d.current_path).exists()
        assert Path(d.current_path) == src  # 回到原位（无同名冲突）
    finally:
        db.close()


def test_batch_reprocess_rejects_archived(tmp_path, monkeypatch):
    """已归档文档不允许重跑。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)

    doc, _ = _make_pending_doc(tmp_path)
    db = SessionLocal()
    try:
        d = db.get(Document, doc.id)
        d.status = STATUS_ARCHIVED
        db.commit()
    finally:
        db.close()

    res = client.post("/api/documents/batch-reprocess", json={"doc_ids": [doc.id]})
    body = res.json()
    assert body["success_count"] == 0
    assert body["failed_count"] == 1
    assert "状态不可重跑" in body["results"][0]["error"]


def test_batch_reprocess_missing_file(tmp_path, monkeypatch):
    """文件已丢失：重跑返回明确错误而非崩溃。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)

    doc, _ = _make_pending_doc(tmp_path)
    db = SessionLocal()
    try:
        d = db.get(Document, doc.id)
        d.current_path = str(tmp_path / "inbox" / "不存在的文件.pdf")
        db.commit()
    finally:
        db.close()

    res = client.post("/api/documents/batch-reprocess", json={"doc_ids": [doc.id]})
    body = res.json()
    assert body["success_count"] == 0
    assert "不存在" in body["results"][0]["error"]
