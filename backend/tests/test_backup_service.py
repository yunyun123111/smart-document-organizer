"""备份 / 恢复服务测试（P0-3）。

覆盖：
- 创建备份：zip 含 app.db / meta.json，计数正确
- 恢复往返：清空数据后从备份恢复，数据完整回来
- 包含归档文档的备份与恢复
- 删除 / 列表 / 非法文件名防御
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.config import settings
from backend.database import SessionLocal
from backend.models import STATUS_ARCHIVED, Document
from backend.services import backup_service


@pytest.fixture(autouse=True)
def _isolate_doc_root(tmp_path, monkeypatch):
    """备份/恢复涉及归档目录，隔离到临时目录避免污染项目 data/。"""
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    settings.document_root.mkdir(parents=True, exist_ok=True)
    yield


def _add_docs(count: int) -> None:
    with SessionLocal() as db:
        for i in range(count):
            db.add(
                Document(
                    original_filename=f"doc{i}.pdf",
                    current_filename=f"doc{i}.pdf",
                    original_path=f"/inbox/doc{i}.pdf",
                    current_path=f"/inbox/doc{i}.pdf",
                    file_type="pdf",
                    file_size=100 + i,
                    file_hash=f"hash{i}",
                    document_type="发票",
                    title=f"发票{i}",
                    status=STATUS_ARCHIVED,
                )
            )
        db.commit()


def _doc_count() -> int:
    with SessionLocal() as db:
        return db.query(Document).count()


class TestCreate:
    def test_create_contains_db_and_meta(self):
        _add_docs(3)
        info = backup_service.create_backup()
        assert info["filename"].startswith("backup_")
        path = backup_service.get_backup_path(info["filename"])
        assert path is not None and path.exists()
        assert info["documents_count"] == 3

        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "app.db" in names
            assert "meta.json" in names
            meta = json.loads(zf.read("meta.json").decode("utf-8"))
            assert meta["documents_count"] == 3

    def test_create_includes_env(self):
        _add_docs(1)
        info = backup_service.create_backup()
        path = backup_service.get_backup_path(info["filename"])
        with zipfile.ZipFile(path) as zf:
            assert ".env" in zf.namelist()

    def test_create_with_documents(self, tmp_path):
        (settings.document_root / "合同" / "销售合同").mkdir(parents=True, exist_ok=True)
        (settings.document_root / "合同" / "销售合同" / "a.pdf").write_bytes(b"PDFDATA")
        _add_docs(1)
        info = backup_service.create_backup(include_documents=True)
        path = backup_service.get_backup_path(info["filename"])
        with zipfile.ZipFile(path) as zf:
            assert "documents/合同/销售合同/a.pdf" in zf.namelist()


class TestRestore:
    def test_restore_roundtrip_database(self):
        _add_docs(2)
        info = backup_service.create_backup()
        path = backup_service.get_backup_path(info["filename"])

        # 清空数据，模拟损坏/清空
        with SessionLocal() as db:
            db.execute(text("DELETE FROM documents"))
            db.commit()
        assert _doc_count() == 0

        result = backup_service.restore_backup(path)
        assert result["ok"] is True
        assert _doc_count() == 2  # 数据完整回来

    def test_restore_documents(self, tmp_path):
        (settings.document_root / "财务" / "发票").mkdir(parents=True, exist_ok=True)
        f = settings.document_root / "财务" / "发票" / "inv.pdf"
        f.write_bytes(b"INV")
        _add_docs(1)
        info = backup_service.create_backup(include_documents=True)
        path = backup_service.get_backup_path(info["filename"])

        # 删除文档目录再恢复
        import shutil
        shutil.rmtree(settings.document_root)
        settings.document_root.mkdir(parents=True, exist_ok=True)

        result = backup_service.restore_backup(path)
        assert result["restored_documents"] == 1
        assert (settings.document_root / "财务" / "发票" / "inv.pdf").read_bytes() == b"INV"

    def test_restore_invalid_zip(self, tmp_path):
        bad = tmp_path / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("other.txt", "x")
        with pytest.raises(ValueError):
            backup_service.restore_backup(bad)


class TestManage:
    def test_list_and_delete(self):
        _add_docs(1)
        info = backup_service.create_backup()
        names = [b["filename"] for b in backup_service.list_backups()]
        assert info["filename"] in names

        assert backup_service.delete_backup(info["filename"]) is True
        assert backup_service.get_backup_path(info["filename"]) is None

    def test_path_traversal_defense(self):
        assert backup_service.delete_backup("../evil.zip") is False
        assert backup_service.get_backup_path("..\\..\\evil.zip") is None
