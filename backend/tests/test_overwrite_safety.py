"""V1.0 稳定性修复·任务2：文件覆盖风险全面验证。

覆盖场景：自动归档 / 人工审核归档 / 批量并发归档 / 邮箱附件归档 / 撤销后再次归档 /
同名多次归档 / 同名跨分类归档 / 文件名清洗后同名 / 分类变更路径冲突 / 回收站恢复同名 /
网页上传同名 / 手动重命名冲突 / 底层 move_file 拒绝覆盖。

核心断言：任何场景下已有文件绝不因新归档/重命名/恢复/上传而被覆盖。
"""
from __future__ import annotations

import email
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.models import Document
from backend.services.archive_service import ArchiveService
from backend.services.file_service import FileOperationError, move_file
from backend.services.recycle_service import recycle_service
from backend.services.undo_service import undo_service
from backend.utils.filename_utils import safe_filename, unique_filename


@pytest.fixture
def archive(db, tmp_path):
    root = tmp_path / "docroot"
    return ArchiveService(db, document_root=root)


@pytest.fixture
def make_file(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    def _make(name, content="合同内容 销售合同"):
        p = inbox / name
        p.write_text(content, encoding="utf-8")
        return p

    return _make


@pytest.fixture
def client(tmp_path, monkeypatch):
    """API 测试：独立目录 + 不持久化配置。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "RECYCLE_BIN_ROOT", str(tmp_path / "recycle_bin"))
    monkeypatch.setattr(settings, "ALLOW_OVERWRITE", False)
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    Path(settings.document_root).mkdir(parents=True, exist_ok=True)
    Path(settings.recycle_bin_root).mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


class TestSameNameArchive:
    def test_archive_same_name_twice_increments(self, archive, make_file):
        """同名文件多次归档：_001 递增，绝不覆盖。"""
        r1 = archive.archive(make_file("合同.pdf", "内容甲"), "合同/销售合同", "合同.pdf")
        r2 = archive.archive(make_file("合同.pdf", "内容乙"), "合同/销售合同", "合同.pdf")
        assert r1.success and r2.success
        names = sorted(p.name for p in r1.document_path.parent.iterdir())
        assert "合同.pdf" in names and "合同_001.pdf" in names
        # 两个文件内容都完好
        assert r1.document_path.read_text(encoding="utf-8") == "内容甲"
        assert r2.document_path.read_text(encoding="utf-8") == "内容乙"

    def test_archive_same_name_three_times(self, archive, make_file):
        paths = []
        for i in range(3):
            r = archive.archive(make_file("x.pdf", f"内容{i}"), "其他", "x.pdf")
            assert r.success
            paths.append(r.document_path.name)
        assert sorted(paths) == ["x.pdf", "x_001.pdf", "x_002.pdf"]

    def test_same_name_cross_category_no_conflict(self, archive, make_file):
        """同名文件归档到不同分类：各自目录独立，不互相覆盖。"""
        r1 = archive.archive(make_file("发票.pdf", "发票内容"), "财务/发票", "发票.pdf")
        r2 = archive.archive(make_file("发票.pdf", "合同内容"), "合同/销售合同", "发票.pdf")
        assert r1.success and r2.success
        assert r1.document_path.parent != r2.document_path.parent
        assert r1.document_path.name == "发票.pdf"
        assert r2.document_path.name == "发票.pdf"


class TestSanitizedAndCategory:
    def test_sanitized_same_name_increments(self, archive, make_file):
        """文件名清洗后变成同名：非法字符被清除，两者落到同目录时递增不覆盖。"""
        # 不同源文件，归档时指定的目标文件名含非法字符（清洗后同为"报价单.pdf"）
        r1 = archive.archive(make_file("源A.pdf", "内容甲"), "业务/报价", "报价:单.pdf")
        r2 = archive.archive(make_file("源B.pdf", "内容乙"), "业务/报价", "报价*单.pdf")
        assert safe_filename("报价:单", ".pdf") == safe_filename("报价*单", ".pdf")
        assert r1.success and r2.success
        names = sorted(p.name for p in r1.document_path.parent.iterdir())
        assert names == ["报价单.pdf", "报价单_001.pdf"]

    def test_category_change_path_conflict_increments(self, archive, make_file):
        """人工审核改分类后归档到已有同名文件的目录：递增不覆盖。"""
        # 第一份误入"其他"，人工修正后仍归档到"财务/银行对账"（与第二份同目录）
        r1 = archive.archive(make_file("对账单.pdf", "一月对账"), "财务/银行对账", "对账单.pdf")
        r2 = archive.archive(make_file("对账单.pdf", "二月对账"), "财务/银行对账", "对账单.pdf")
        assert r1.success and r2.success
        assert r1.document_path.read_text(encoding="utf-8") == "一月对账"
        assert r2.document_path.read_text(encoding="utf-8") == "二月对账"
        assert r1.document_path.name != r2.document_path.name


class TestUndoOccupied:
    def _archived_log(self, db, archive, src):
        r = archive.archive(src, "其他", src.name)
        assert r.success
        from backend.models import OP_ARCHIVE, OperationLog

        log = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_ARCHIVE)
            .order_by(OperationLog.id.desc())
            .first()
        )
        return log

    def test_undo_occupied_old_path_increments(self, db, archive, make_file):
        """撤销时原位置被占用：递增命名恢复，绝不覆盖原位置文件。"""
        src = make_file("undo.pdf", "待撤销内容")
        log = self._archived_log(db, archive, src)
        # 原位置被一个新文件占用
        src.write_text("后来放入的同名文件", encoding="utf-8")
        ur = undo_service.undo(db, log.id)
        assert ur.success
        # 原文件仍在（未被覆盖）
        assert src.read_text(encoding="utf-8") == "后来放入的同名文件"
        # 归档文件被恢复为递增名
        assert ur.restored_path is not None
        assert ur.restored_path.name == "undo_001.pdf"
        assert ur.restored_path.read_text(encoding="utf-8") == "待撤销内容"

    def test_undo_occupied_restores_document_state(self, db, archive, make_file):
        """撤销后文档记录回写实际恢复路径（_001），而非被占用的原路径。"""
        src = make_file("st.pdf", "状态内容")
        r = archive.archive(src, "其他", "st.pdf")
        from backend.models import OP_ARCHIVE, OperationLog

        log = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_ARCHIVE)
            .order_by(OperationLog.id.desc())
            .first()
        )
        src.write_text("占用文件", encoding="utf-8")
        ur = undo_service.undo(db, log.id)
        assert ur.success
        db.expire_all()
        doc = db.get(Document, r.document_id)
        assert doc.status == "pending"
        assert doc.current_filename == "st_001.pdf"
        assert Path(doc.current_path).read_text(encoding="utf-8") == "状态内容"


class TestRecycleRestore:
    def test_restore_cross_category_no_overwrite(self, db, tmp_path):
        """回收站恢复时原位置已有同名文件：递增恢复（跨分类场景补强）。"""
        from backend.models import STATUS_ARCHIVED, Document

        def _mkdoc(name, content, path):
            p = tmp_path / "docs" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            d = Document(
                original_filename=name, current_filename=name,
                original_path=str(p), current_path=str(p),
                file_type="pdf", status=STATUS_ARCHIVED, extracted_text="",
            )
            db.add(d)
            db.commit()
            db.refresh(d)
            return d

        doc = _mkdoc("结算单.pdf", "回收内容", "分类A")
        r = recycle_service.move_to_recycle(db, doc)
        item = db.get(__import__("backend.models", fromlist=["RecycleBinItem"]).RecycleBinItem, r.recycle_id)
        # 原路径被新文件占用
        Path(item.original_path).write_text("新文件", encoding="utf-8")
        rr = recycle_service.restore(db, item)
        assert rr.success
        restored = Path(item.original_path)
        assert restored.read_text(encoding="utf-8") == "新文件", "不能覆盖已有文件"
        assert (restored.parent / "结算单_001.pdf").read_text(encoding="utf-8") == "回收内容"


class TestUploadAndEmail:
    def test_upload_same_name_increments(self, client):
        """网页上传同名文件：inbox 内递增，不覆盖。"""
        data = {"file": ("同名.pdf", "内容甲".encode(), "application/pdf")}
        r1 = client.post("/api/documents/upload", files=data)
        data2 = {"file": ("同名.pdf", "内容乙".encode(), "application/pdf")}
        r2 = client.post("/api/documents/upload", files=data2)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["filename"] != r2.json()["filename"]
        assert r1.json()["filename"].endswith(".pdf")
        assert "_001" in r2.json()["filename"] or "_1" in r2.json()["filename"]

    def test_email_attachment_same_name_increments(self, tmp_path, monkeypatch):
        """邮箱重复附件名：落 inbox 唯一命名，不覆盖。"""
        from backend.services import email_ingest

        monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
        Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)

        def _msg(payload: bytes, fname: str):
            m = email.message.EmailMessage()
            m["Subject"] = "附件"
            m["Message-ID"] = f"<{fname}@test>"
            m.add_attachment(payload, maintype="application", subtype="pdf", filename=fname)
            return m

        m1 = _msg(b"PDF-A", "结算.pdf")
        m2 = _msg(b"PDF-B", "结算.pdf")
        files1 = email_ingest._save_attachments(m1)
        files2 = email_ingest._save_attachments(m2)
        assert len(files1) == 1 and len(files2) == 1
        assert files1[0].read_bytes() == b"PDF-A"
        assert files2[0].read_bytes() == b"PDF-B"
        assert files1[0].name != files2[0].name


class TestRenameAndMove:
    def test_manual_rename_conflict_increments(self, client):
        """手动重命名到已被占用的名字：递增不覆盖。"""
        from backend.models import STATUS_ARCHIVED
        from backend.services import duplicate_service

        with __import__("backend.database", fromlist=["SessionLocal"]).SessionLocal() as db:
            doc = Document(
                original_filename="原名.pdf", current_filename="原名.pdf",
                original_path=str(Path(settings.document_root) / "原名.pdf"),
                current_path=str(Path(settings.document_root) / "原名.pdf"),
                file_type="pdf", status=STATUS_ARCHIVED, extracted_text="",
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            doc_id = doc.id
        Path(doc.current_path).write_text("原文件", encoding="utf-8")
        # 先造一个占用"目标名.pdf"的文件
        (Path(settings.document_root) / "目标名.pdf").write_text("占用", encoding="utf-8")
        r = client.post(f"/api/documents/{doc_id}/rename", json={"filename": "目标名"})
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "目标名_001.pdf"
        assert (Path(settings.document_root) / "目标名.pdf").read_text(encoding="utf-8") == "占用"

    def test_move_file_refuses_overwrite(self, tmp_path):
        """底层 move_file 默认拒绝覆盖，抛明确错误且源文件不动。"""
        src = tmp_path / "a.pdf"
        dst = tmp_path / "b.pdf"
        src.write_text("源内容", encoding="utf-8")
        dst.write_text("已有内容", encoding="utf-8")
        with pytest.raises(FileOperationError):
            move_file(src, dst)
        assert src.read_text(encoding="utf-8") == "源内容"
        assert dst.read_text(encoding="utf-8") == "已有内容"


class TestConcurrent:
    def test_batch_concurrent_same_name_no_overwrite(self, db, tmp_path):
        """批量并发归档同名文件：锁保护临界区，绝不覆盖。"""
        from backend.models import STATUS_ARCHIVED
        from backend.services.processing_service import processing_service

        # 直接并发调用 archive（模拟批量 worker），各文件内容不同避免 hash 判重
        def _one(i):
            from backend.database import SessionLocal

            with SessionLocal() as s:
                root = tmp_path / "docroot"
                src = tmp_path / "inbox" / f"f{i}.pdf"
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text(f"内容{i}", encoding="utf-8")
                ar = ArchiveService(s, document_root=root)
                res = ar.archive(src, "合同/销售合同", "并发合同.pdf")
                return res

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(_one, range(4)))
        assert all(r.success for r in results)
        names = sorted(p.name for p in results[0].document_path.parent.iterdir())
        assert len(names) == 4
        assert names == ["并发合同.pdf", "并发合同_001.pdf", "并发合同_002.pdf", "并发合同_003.pdf"]
        # 内容无覆盖
        contents = {p.read_text(encoding="utf-8") for p in results[0].document_path.parent.iterdir()}
        assert contents == {"内容0", "内容1", "内容2", "内容3"}
