"""Phase 10 归档服务测试：归档/去重/覆盖保护/撤销/日志。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backend.models import OP_ARCHIVE, OP_UNDO, Document, OperationLog
from backend.services.archive_service import ArchiveService
from backend.services.undo_service import undo_service


@pytest.fixture
def archive(db: Session, tmp_path: Path) -> ArchiveService:
    root = tmp_path / "docroot"
    return ArchiveService(db, document_root=root)


@pytest.fixture
def make_file(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    def _make(name: str, content: str = "合同内容 销售合同") -> Path:
        p = inbox / name
        p.write_text(content, encoding="utf-8")
        return p

    return _make


class TestArchive:
    def test_archive_moves_file(self, archive, make_file):
        src = make_file("a.pdf", "销售合同 XS001")
        result = archive.archive(
            src, "合同/销售合同", "2026-08-20_销售合同_ABC_XS001.pdf",
            date_str="2026-08-20",
        )
        assert result.success
        assert not src.exists(), "源文件应被移动"
        assert result.document_path.exists()
        # 目录结构：docroot/合同/销售合同/2026/08
        assert result.document_path.parent == archive.document_root / "合同/销售合同/2026/08"

    def test_archive_records_log(self, db, archive, make_file):
        src = make_file("b.pdf")
        archive.archive(src, "财务/发票", "发票.pdf", date_str="2026-08-20")
        logs = db.query(OperationLog).filter(OperationLog.operation_type == OP_ARCHIVE).all()
        assert len(logs) == 1
        assert logs[0].old_path == str(src)
        assert logs[0].new_path.endswith("发票.pdf")
        assert logs[0].result == "ok"

    def test_archive_no_date_no_subdir(self, archive, make_file):
        src = make_file("c.pdf")
        result = archive.archive(src, "项目资料", "资料.pdf", date_str=None)
        assert result.success
        assert result.document_path.parent == archive.document_root / "项目资料"

    def test_archive_missing_source(self, archive):
        result = archive.archive("C:/not/exist.pdf", "其他", "x.pdf")
        assert not result.success
        assert "不存在" in result.error

    def test_archive_target_exists_increments(self, archive, make_file):
        src = make_file("d.pdf")
        r1 = archive.archive(src, "其他", "文件.pdf")
        assert r1.success
        src2 = make_file("d2.pdf", "不同内容")
        r2 = archive.archive(src2, "其他", "文件.pdf")
        assert r2.success
        assert r2.document_path.name == "文件_001.pdf"


class TestDuplicate:
    def test_duplicate_detected(self, db, archive, make_file):
        src = make_file("e.pdf", "完全一样的内容")
        r1 = archive.archive(src, "其他", "e.pdf")
        assert r1.success

        # 相同内容的文件再次归档 -> 重复
        src2 = make_file("e2.pdf", "完全一样的内容")
        r2 = archive.archive(src2, "其他", "e2.pdf")
        assert not r2.success
        assert r2.duplicate is True
        assert r2.duplicate_of is not None

    def test_different_content_not_duplicate(self, db, archive, make_file):
        src = make_file("f.pdf", "内容一")
        archive.archive(src, "其他", "f.pdf")
        src2 = make_file("f2.pdf", "内容二完全不同")
        r2 = archive.archive(src2, "其他", "f2.pdf")
        assert r2.success
        assert r2.duplicate is False


class TestUndo:
    def test_undo_archive(self, db, archive, make_file):
        src = make_file("g.pdf", "可撤销内容")
        r = archive.archive(src, "其他", "g.pdf", date_str="2026-08-20")
        assert r.success

        log = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_ARCHIVE)
            .order_by(OperationLog.id.desc())
            .first()
        )
        ur = undo_service.undo(db, log.id)
        assert ur.success
        assert src.exists(), "撤销后文件应回到原位置"
        assert not r.document_path.exists(), "归档位置不应再有文件"

    def test_undo_missing_file(self, db, archive, make_file):
        src = make_file("h.pdf", "内容")
        r = archive.archive(src, "其他", "h.pdf")
        assert r.success
        log = db.query(OperationLog).filter_by(operation_type=OP_ARCHIVE).first()
        r.document_path.unlink()  # 归档文件被外部删除
        ur = undo_service.undo(db, log.id)
        assert not ur.success
        assert "不存在" in ur.error

    def test_undo_records_undo_log(self, db, archive, make_file):
        src = make_file("i.pdf", "内容")
        archive.archive(src, "其他", "i.pdf")
        log = db.query(OperationLog).filter_by(operation_type=OP_ARCHIVE).first()
        undo_service.undo(db, log.id)
        undo_logs = (
            db.query(OperationLog).filter(OperationLog.operation_type == OP_UNDO).all()
        )
        assert len(undo_logs) == 1

    def test_undo_non_reversible(self, db):
        from backend.services.operation_service import log_operation

        log = log_operation(db, "IMPORT", old_path="a", new_path="b")
        ur = undo_service.undo(db, log.id)
        assert not ur.success
        assert "不可撤销" in ur.error

    def test_undo_restores_document_state(self, db, archive, make_file):
        """撤销后文档状态必须回到 pending 且路径复位（否则会被误判为重复）。"""
        src = make_file("j.pdf", "撤销状态内容")
        r = archive.archive(src, "其他", "j.pdf", document_id=None)
        assert r.success
        doc = db.get(Document, r.document_id)
        assert doc.status == "archived"
        assert doc.current_path == str(r.document_path)

        # 归档文件被去重服务识别（status=archived）
        from backend.services.duplicate_service import duplicate_service

        assert duplicate_service.find_duplicate(db, doc.file_hash) is not None

        log = db.query(OperationLog).filter_by(operation_type=OP_ARCHIVE).first()
        ur = undo_service.undo(db, log.id)
        assert ur.success

        db.expire_all()
        doc = db.get(Document, r.document_id)
        assert doc.status == "pending", "撤销后应回到待整理状态"
        assert doc.current_path == str(src)
        assert doc.current_filename == src.name
        # 回到 inbox 的文件不应再被当作已归档重复项
        assert duplicate_service.find_duplicate(db, doc.file_hash) is None


class TestArchiveSafety:
    """归档入参可能来自请求体（人工审核指定分类/文件名），必须有防护。"""

    def test_category_path_traversal_rejected(self, archive, make_file):
        src = make_file("t.pdf", "穿越内容")
        r = archive.archive(src, "../../../evil", "t.pdf")
        assert not r.success
        assert "分类路径非法" in r.error
        assert src.exists(), "被拒绝的归档不应移动文件"

    def test_absolute_category_rejected(self, archive, make_file):
        src = make_file("abs.pdf", "绝对路径内容")
        r = archive.archive(src, "C:/Windows/Temp", "abs.pdf")
        assert not r.success
        assert src.exists()

    def test_filename_is_sanitized(self, archive, make_file):
        """文件名里的路径分隔符与非法字符应被清除，且不得逃出归档目录。"""
        src = make_file("f.pdf", "文件名清洗")
        r = archive.archive(src, "其他", "../evil/x.pdf")
        assert r.success
        assert r.document_path.parent == archive.document_root / "其他"
        assert r.document_path.suffix == ".pdf"

    def test_missing_document_id_rejected_without_moving(self, archive, make_file):
        """document_id 指向不存在的记录时应拒绝，而不是搬完文件再插空记录。"""
        src = make_file("m.pdf", "空记录")
        r = archive.archive(src, "其他", "m.pdf", document_id=999999)
        assert not r.success
        assert "文档记录不存在" in r.error
        assert src.exists(), "拒绝归档时原文件必须留在原处"

    def test_allow_overwrite_replaces_target(self, db, tmp_path, make_file):
        """ALLOW_OVERWRITE=True 时同名文件应覆盖成功（历史上此处必然失败）。"""
        root = tmp_path / "docroot2"
        svc = ArchiveService(db, document_root=root, allow_overwrite=True)
        src1 = make_file("o1.pdf", "第一份内容")
        r1 = svc.archive(src1, "其他", "same.pdf")
        assert r1.success
        src2 = make_file("o2.pdf", "第二份内容不同")
        r2 = svc.archive(src2, "其他", "same.pdf")
        assert r2.success, r2.error
        assert r2.document_path.read_text(encoding="utf-8") == "第二份内容不同"
        # 覆盖完成后不应留下 .replacing 备份残留
        leftovers = [p.name for p in r2.document_path.parent.iterdir()]
        assert leftovers == ["same.pdf"], f"目录残留了备份文件: {leftovers}"
