"""V1.5-01 回收站服务与 API 测试。

覆盖：移入回收站（文件移动/记录保留/状态变更）、恢复（原状态/原路径/同名不覆盖）、
永久删除、清空、批量、异常（文件缺失/目录自动创建）、安全（文档库/统计/整理排除）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.config import settings
from backend.main import app
from backend.models import (
    OP_DELETE_TO_RECYCLE,
    OP_PERMANENT_DELETE,
    OP_RESTORE_FROM_RECYCLE,
    STATUS_ARCHIVED,
    STATUS_RECYCLED,
    Document,
    OperationLog,
    RecycleBinItem,
)
from backend.services.recycle_service import recycle_service


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立临时库 + 独立目录 + 不持久化配置。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "RECYCLE_BIN_ROOT", str(tmp_path / "recycle_bin"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    Path(settings.document_root).mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


def _make_doc(db: Session, root: Path, name: str = "合同A.pdf",
              content: str = "销售合同内容", status: str = STATUS_ARCHIVED,
              doc_type: str = "销售合同", path: str | None = None) -> tuple[Document, Path]:
    """创建一条文档记录 + 磁盘文件（默认已归档）。"""
    src = Path(path) if path else root / "docs" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(content, encoding="utf-8")
    doc = Document(
        original_filename=name,
        current_filename=name,
        original_path=str(src),
        current_path=str(src),
        file_type="pdf",
        file_size=src.stat().st_size,
        document_type=doc_type,
        status=status,
        extracted_text="",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc, src


class TestMoveToRecycle:
    def test_delete_enters_recycle(self, db, tmp_path):
        doc, src = _make_doc(db, tmp_path)
        res = recycle_service.move_to_recycle(db, doc, reason="测试删除")
        assert res.success
        assert res.recycle_id is not None
        assert res.file_moved

        # 文件实际移动到回收站目录（recycle_bin/{年}/{月}/）
        assert not src.exists(), "原文件应被移动走"
        item = db.get(RecycleBinItem, res.recycle_id)
        assert item is not None
        assert Path(item.recycle_path).is_file()
        assert item.document_id == doc.id
        assert item.original_status == STATUS_ARCHIVED
        assert item.original_file_missing is False
        # 文件名带 document_id 前缀，避免同名冲突
        assert Path(item.recycle_path).name.startswith(f"{doc.id}_")

        # documents 记录保留，状态改为 recycled
        doc2 = db.get(Document, doc.id)
        assert doc2 is not None
        assert doc2.status == STATUS_RECYCLED

        # 操作日志
        logs = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_DELETE_TO_RECYCLE)
            .all()
        )
        assert len(logs) == 1
        assert logs[0].old_path == str(src)

    def test_same_name_no_overwrite_in_recycle(self, db, tmp_path):
        """多个同名文件进入回收站互不覆盖。"""
        doc1, _ = _make_doc(db, tmp_path, name="合同A.pdf", content="内容1")
        r1 = recycle_service.move_to_recycle(db, doc1)
        assert r1.success
        doc2, _ = _make_doc(db, tmp_path, name="合同A.pdf", content="内容2")
        r2 = recycle_service.move_to_recycle(db, doc2)
        assert r2.success
        item1 = db.get(RecycleBinItem, r1.recycle_id)
        item2 = db.get(RecycleBinItem, r2.recycle_id)
        assert item1.recycle_path != item2.recycle_path
        assert Path(item1.recycle_path).is_file()
        assert Path(item2.recycle_path).is_file()
        assert Path(item1.recycle_path).read_text(encoding="utf-8") == "内容1"
        assert Path(item2.recycle_path).read_text(encoding="utf-8") == "内容2"

    def test_recycle_dir_auto_created(self, db, tmp_path, monkeypatch):
        """回收站目录不存在时自动创建。"""
        monkeypatch.setattr(settings, "RECYCLE_BIN_ROOT", str(tmp_path / "recycle_bin"))
        rb = tmp_path / "recycle_bin"
        assert not rb.exists()
        doc, _ = _make_doc(db, tmp_path)
        res = recycle_service.move_to_recycle(db, doc)
        assert res.success
        assert rb.exists()

    def test_missing_file_still_records(self, db, tmp_path):
        """原文件不存在：不崩溃，记录仍进回收站并标记缺失。"""
        doc = Document(
            original_filename="丢失.pdf", current_filename="丢失.pdf",
            original_path=str(tmp_path / "not_exist.pdf"),
            current_path=str(tmp_path / "not_exist.pdf"),
            file_type="pdf", status=STATUS_ARCHIVED, extracted_text="",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        res = recycle_service.move_to_recycle(db, doc, reason="缺失文件")
        assert res.success
        assert res.file_moved is False
        item = db.get(RecycleBinItem, res.recycle_id)
        assert item.original_file_missing is True
        assert db.get(Document, doc.id).status == STATUS_RECYCLED

    def test_permission_error_returns_clear_message(self, db, tmp_path, monkeypatch):
        """移动失败应返回明确错误，不抛 500。"""
        import backend.services.recycle_service as recycle_mod

        def _boom(*a, **k):
            from backend.services.file_service import FileOperationError
            raise FileOperationError("没有目标目录写入权限")

        monkeypatch.setattr(recycle_mod, "move_file", _boom)
        doc, src = _make_doc(db, tmp_path)
        res = recycle_service.move_to_recycle(db, doc)
        assert not res.success
        assert "没有目标目录写入权限" in res.error
        assert src.exists(), "失败时原文件必须保留"


class TestRestore:
    def test_restore_restores_state_and_path(self, db, tmp_path):
        doc, src = _make_doc(db, tmp_path, status=STATUS_ARCHIVED)
        res = recycle_service.move_to_recycle(db, doc)
        assert res.success
        item = db.get(RecycleBinItem, res.recycle_id)

        r = recycle_service.restore(db, item)
        assert r.success
        # 文件回到原路径
        assert src.exists()
        # 记录还原原状态
        doc2 = db.get(Document, doc.id)
        assert doc2.status == STATUS_ARCHIVED
        assert doc2.current_path == str(src)
        # 回收站记录删除
        assert db.get(RecycleBinItem, res.recycle_id) is None
        # 日志
        logs = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_RESTORE_FROM_RECYCLE)
            .all()
        )
        assert len(logs) == 1
        assert logs[0].new_path == str(src)

    def test_restore_does_not_overwrite_existing(self, db, tmp_path):
        """原路径已有同名文件：恢复不覆盖，递增后缀。"""
        doc, src = _make_doc(db, tmp_path, name="合同A.pdf")
        res = recycle_service.move_to_recycle(db, doc)
        item = db.get(RecycleBinItem, res.recycle_id)

        # 在原位置放一个同名文件
        src.write_text("已被占用", encoding="utf-8")
        r = recycle_service.restore(db, item)
        assert r.success
        assert src.read_text(encoding="utf-8") == "已被占用", "不能覆盖已有文件"
        # 恢复文件落在递增名
        restored = Path(db.get(Document, doc.id).current_path)
        assert restored.name == "合同A_001.pdf"
        assert restored.read_text(encoding="utf-8") == "销售合同内容"

    def test_restore_missing_recycle_file(self, db, tmp_path):
        """回收站内文件被外部删除：仍可恢复记录，提示文件缺失。"""
        doc, src = _make_doc(db, tmp_path)
        res = recycle_service.move_to_recycle(db, doc)
        item = db.get(RecycleBinItem, res.recycle_id)
        Path(item.recycle_path).unlink()

        r = recycle_service.restore(db, item)
        assert r.success
        assert "文件已丢失" in r.message
        assert db.get(Document, doc.id).status == STATUS_ARCHIVED


class TestPermanentDelete:
    def test_permanent_delete_removes_everything(self, db, tmp_path):
        doc, src = _make_doc(db, tmp_path)
        res = recycle_service.move_to_recycle(db, doc)
        item = db.get(RecycleBinItem, res.recycle_id)
        recycle_file = Path(item.recycle_path)

        r = recycle_service.permanent_delete(db, item)
        assert r.success
        assert not recycle_file.exists(), "回收站文件应被物理删除"
        assert db.get(RecycleBinItem, res.recycle_id) is None
        assert db.get(Document, doc.id) is None, "永久删除后 documents 记录应清除"
        # 操作日志保留（document_id 因 FK SET NULL 置空）
        logs = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_PERMANENT_DELETE)
            .all()
        )
        assert len(logs) == 1
        assert logs[0].old_path == str(src)


class TestBatchAndEmpty:
    def _three_in_recycle(self, db, tmp_path):
        ids = []
        for i, name in enumerate(["a.pdf", "b.pdf", "c.pdf"]):
            doc, _ = _make_doc(db, tmp_path, name=name, content=f"内容{i}")
            r = recycle_service.move_to_recycle(db, doc)
            ids.append(r.recycle_id)
        return ids

    def test_batch_restore(self, db, tmp_path):
        ids = self._three_in_recycle(db, tmp_path)
        items = db.query(RecycleBinItem).all()
        for item in items:
            r = recycle_service.restore(db, item)
            assert r.success
        assert db.query(RecycleBinItem).count() == 0
        assert db.query(Document).filter(Document.status == STATUS_RECYCLED).count() == 0

    def test_empty_recycle(self, db, tmp_path):
        ids = self._three_in_recycle(db, tmp_path)
        result = recycle_service.empty(db)
        assert result["ok"] == 3
        assert result["failed"] == 0
        assert db.query(RecycleBinItem).count() == 0
        assert db.query(Document).count() == 0


class TestExclusion:
    def test_library_list_excludes_recycled(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "RECYCLE_BIN_ROOT", str(tmp_path / "recycle_bin"))
        Path(settings.recycle_bin_root).mkdir(parents=True, exist_ok=True)
        # 创建两份正常文档，删除其中一份 -> 进回收站
        keep, _ = _make_doc(db, tmp_path, name="保留.pdf")
        victim, _ = _make_doc(db, tmp_path, name="待删.pdf")
        r = client.delete(f"/api/documents/{victim.id}")
        assert r.status_code == 200
        assert r.json()["recycled"] is True

        # 文档库列表不应包含回收站文件；其余文档仍可见
        r = client.get("/api/documents")
        assert r.status_code == 200
        listed_ids = {d["id"] for d in r.json()}
        assert victim.id not in listed_ids
        assert keep.id in listed_ids

        # 指定 status=recycled 也无法从文档库 API 查到（回收站走独立页面）
        r = client.get("/api/documents", params={"status": "recycled"})
        assert r.status_code == 200
        assert all(d["id"] != victim.id for d in r.json())

    def test_stats_excludes_recycled(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "RECYCLE_BIN_ROOT", str(tmp_path / "recycle_bin"))
        Path(settings.recycle_bin_root).mkdir(parents=True, exist_ok=True)
        doc, src = _make_doc(db, tmp_path, name="统计1.pdf", doc_type="发票")
        r = client.delete(f"/api/documents/{doc.id}")
        assert r.status_code == 200
        stats = client.get("/api/system/dashboard/stats").json()
        # 删除后 documents 仍有 1 条（recycled），但统计应为 0
        assert stats["total_documents"] == 0
        assert all(t["count"] == 0 or t["type"] != "发票" for t in stats["type_counts"])

    def test_processing_rejects_recycle_dir(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "RECYCLE_BIN_ROOT", str(tmp_path / "recycle_bin"))
        Path(settings.recycle_bin_root).mkdir(parents=True, exist_ok=True)
        r = client.post("/api/processing/start", json={"source_dir": str(settings.recycle_bin_root)})
        assert r.status_code == 400  # 回收站目录不能作为待整理目录
