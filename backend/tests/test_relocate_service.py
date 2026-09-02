# -*- coding: utf-8 -*-
"""文件重定位服务测试：检测失效 + 重新关联（文件名/哈希）。"""
from backend.models import Document
from backend.services.relocate_service import missing_documents, relocate


def _mk(db, filename, path):
    d = Document(
        original_filename=filename, current_filename=filename,
        original_path=path, current_path=path,
        file_hash="hash_abc", document_type="发票",
    )
    db.add(d)
    db.flush()
    return d


def test_missing_detection(tmp_path, db):
    d = _mk(db, "发票a.pdf", str(tmp_path / "gone" / "发票a.pdf"))
    db.commit()
    missing = missing_documents(db)
    assert len(missing) == 1
    assert missing[0]["id"] == d.id


def test_relocate_by_filename(tmp_path, db):
    _mk(db, "发票a.pdf", str(tmp_path / "gone" / "发票a.pdf"))
    db.commit()
    real = tmp_path / "moved" / "发票a.pdf"
    real.parent.mkdir()
    real.write_bytes(b"content")
    res = relocate(db, search_roots=[str(tmp_path / "moved")])
    assert res["relinked"] == 1
    assert res["failed"] == 0
    db.refresh(db.query(Document).first())
    assert db.query(Document).first().current_path == str(real)


def test_relocate_unmatched(tmp_path, db):
    _mk(db, "发票a.pdf", str(tmp_path / "gone" / "发票a.pdf"))
    db.commit()
    res = relocate(db, search_roots=[str(tmp_path)])
    assert res["relinked"] == 0
    assert res["failed"] == 1


def test_relocate_no_missing(db):
    res = relocate(db)
    assert res["total"] == 0
