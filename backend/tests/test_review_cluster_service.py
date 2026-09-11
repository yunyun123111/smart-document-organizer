"""待审核单据指纹聚类与批量继承服务测试（V5 步骤）。

覆盖：
- 指纹聚类：相同指纹归簇、不同指纹分簇、按数量降序
- 指纹即时计算：无指纹文档自动计算并落库（mock 指纹服务，避免真实 OCR）
- 批量继承归档：基准分类继承、状态变更、模板学习调用
- 容错：目标不存在 / 非待审核 / 归档失败 → 计入 failed
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    STATUS_ARCHIVED,
    STATUS_NEED_REVIEW,
    Document,
    DocumentFingerprint,
    DocumentField,
)
from backend.services import review_cluster_service as rc_mod
from backend.services.review_cluster_service import ReviewClusterService

TEXT_LAYER_META = {"source": "text_layer", "block_count": 5, "matched_keywords": ["合同"]}


def _make_doc(
    db: Session,
    filename: str = "a.pdf",
    status: str = STATUS_NEED_REVIEW,
    with_fields: bool = True,
    path: Path | None = None,
) -> Document:
    p = path or Path("/tmp") / filename
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"test-content-" + filename.encode("utf-8"))
    doc = Document(
        original_filename=filename,
        current_filename=filename,
        original_path=str(p),
        current_path=str(p),
        file_type="pdf",
        document_type="销售合同",
        status=status,
        confidence=0.9,
    )
    db.add(doc)
    db.flush()
    if with_fields:
        db.add(
            DocumentField(
                document_id=doc.id,
                field_name="suggested_category",
                field_value="合同/销售合同",
            )
        )
        db.add(
            DocumentField(
                document_id=doc.id,
                field_name="contract_no",
                field_value="SJWLXS-2026-YC0001",
            )
        )
    db.commit()
    return doc


def _add_fingerprint(db: Session, doc: Document, layout: str, simhash: str) -> DocumentFingerprint:
    fp = DocumentFingerprint(
        document_id=doc.id,
        layout_hash=layout,
        text_simhash=simhash,
        feature_meta=TEXT_LAYER_META,
    )
    db.add(fp)
    db.commit()
    return fp


class TestClustering:
    def test_same_fingerprint_same_cluster(self, db):
        d1 = _make_doc(db, "a.pdf")
        d2 = _make_doc(db, "b.pdf")
        _add_fingerprint(db, d1, "L1", "1111111111111111")
        _add_fingerprint(db, d2, "L1", "1111111111111111")  # 版面+SimHash 完全一致

        clusters = ReviewClusterService.get_clustered_pending_reviews(db)
        assert len(clusters) == 1
        assert clusters[0]["count"] == 2
        assert "base_fingerprint" not in clusters[0]  # 内部对象已剔除
        assert len(clusters[0]["documents"]) == 2

    def test_different_fingerprint_different_cluster(self, db):
        d1 = _make_doc(db, "a.pdf")
        d2 = _make_doc(db, "b.pdf")
        _add_fingerprint(db, d1, "L1", "1111111111111111")
        _add_fingerprint(db, d2, "L2", "ffffffffffffffff")  # 版面+SimHash 均不同

        clusters = ReviewClusterService.get_clustered_pending_reviews(db)
        assert len(clusters) == 2

    def test_clusters_sorted_by_count_desc(self, db):
        d1 = _make_doc(db, "a.pdf")
        d2 = _make_doc(db, "b.pdf")
        d3 = _make_doc(db, "c.pdf")
        _add_fingerprint(db, d1, "L1", "1111111111111111")
        _add_fingerprint(db, d2, "L1", "1111111111111111")
        _add_fingerprint(db, d3, "L2", "ffffffffffffffff")

        clusters = ReviewClusterService.get_clustered_pending_reviews(db)
        assert clusters[0]["count"] == 2
        assert clusters[1]["count"] == 1

    def test_pending_review_only(self, db):
        d1 = _make_doc(db, "a.pdf", status=STATUS_NEED_REVIEW)
        d2 = _make_doc(db, "b.pdf", status=STATUS_ARCHIVED)  # 已归档不参与
        _add_fingerprint(db, d1, "L1", "1111111111111111")
        _add_fingerprint(db, d2, "L1", "1111111111111111")

        clusters = ReviewClusterService.get_clustered_pending_reviews(db)
        assert len(clusters) == 1
        assert clusters[0]["count"] == 1

    def test_compute_fingerprint_on_demand(self, db, monkeypatch):
        """无指纹文档：自动计算并落库（mock 指纹服务，避免真实 OCR）。"""
        doc = _make_doc(db, "scan.pdf")

        def fake_extract(path):
            return ("L99", "9999999999999999", {"source": "ocr", "block_count": 3})

        monkeypatch.setattr(rc_mod.FingerprintService, "extract_from_pdf", fake_extract)

        clusters = ReviewClusterService.get_clustered_pending_reviews(db)
        assert len(clusters) == 1
        assert clusters[0]["count"] == 1
        # 指纹已落库
        fp = (
            db.query(DocumentFingerprint)
            .filter(DocumentFingerprint.document_id == doc.id)
            .first()
        )
        assert fp is not None
        assert fp.layout_hash == "L99"
        assert fp.feature_meta["source"] == "ocr"

    def test_uncomputable_fingerprint_keeps_solo_cluster(self, db, monkeypatch):
        """指纹不可计算（无文本+OCR空）：不落库、独立成簇、不崩溃。"""
        doc = _make_doc(db, "empty.pdf")

        def fake_extract(path):
            return ("0" * 64, "0" * 16, {"source": "empty", "block_count": 0})

        monkeypatch.setattr(rc_mod.FingerprintService, "extract_from_pdf", fake_extract)

        clusters = ReviewClusterService.get_clustered_pending_reviews(db)
        assert len(clusters) == 1
        assert clusters[0]["count"] == 1
        assert (
            db.query(DocumentFingerprint)
            .filter(DocumentFingerprint.document_id == doc.id)
            .first()
            is None
        )


class TestBatchConfirm:
    @staticmethod
    def _ok_archive(self, src, category_path, filename, date_str=None, document_id=None, job_id=None):
        from backend.services.archive_service import ArchiveResult
        # 模拟真实 ArchiveService 的行为：归档成功 → 状态 archived + 更新路径
        doc = self.db.get(Document, document_id)
        if doc is not None:
            doc.status = STATUS_ARCHIVED
            doc.current_path = str(Path("/tmp/archived") / filename)
        self.db.commit()
        return ArchiveResult(success=True, document_path=Path("/tmp/archived") / filename, document_id=document_id)

    def test_batch_confirm_inherits_base_category(self, db, monkeypatch):
        base = _make_doc(db, "base.pdf")
        t1 = _make_doc(db, "t1.pdf")
        t2 = _make_doc(db, "t2.pdf")

        monkeypatch.setattr(rc_mod.ArchiveService, "archive", self._ok_archive)
        monkeypatch.setattr(
            rc_mod, "_render_suggestion", lambda db, doc, category_path, fields: "t_renamed.pdf"
        )
        monkeypatch.setattr(rc_mod, "_learn_template", lambda db, doc, category_path: None)

        result = ReviewClusterService.batch_confirm_cluster(db, base.id, [t1.id, t2.id])
        assert result["success_count"] == 2
        assert result["failed_count"] == 0
        # 状态已归档，且分类已落库
        for tid in (t1.id, t2.id):
            doc = db.get(Document, tid)
            assert doc.status == STATUS_ARCHIVED
            field = (
                db.query(DocumentField)
                .filter(
                    DocumentField.document_id == tid,
                    DocumentField.field_name == "suggested_category",
                )
                .first()
            )
            assert field.field_value == "合同/销售合同"

    def test_batch_confirm_skips_invalid_targets(self, db, monkeypatch):
        base = _make_doc(db, "base.pdf")
        ok = _make_doc(db, "ok.pdf")
        already = _make_doc(db, "done.pdf", status=STATUS_ARCHIVED)  # 非待审核 → 跳过

        monkeypatch.setattr(rc_mod.ArchiveService, "archive", self._ok_archive)
        monkeypatch.setattr(rc_mod, "_render_suggestion", lambda *a, **k: "x.pdf")
        monkeypatch.setattr(rc_mod, "_learn_template", lambda *a, **k: None)

        result = ReviewClusterService.batch_confirm_cluster(db, base.id, [ok.id, already.id, 9999])
        assert result["success_count"] == 1
        assert result["failed_count"] == 2
        assert result["total"] == 3

    def test_batch_confirm_archive_failure_reported(self, db, monkeypatch):
        base = _make_doc(db, "base.pdf")
        t1 = _make_doc(db, "t1.pdf")

        def fail_archive(self, src, category_path, filename, date_str=None, document_id=None, job_id=None):
            from backend.services.archive_service import ArchiveResult
            return ArchiveResult(success=False, error="磁盘满")

        monkeypatch.setattr(rc_mod.ArchiveService, "archive", fail_archive)
        monkeypatch.setattr(rc_mod, "_render_suggestion", lambda *a, **k: "x.pdf")
        monkeypatch.setattr(rc_mod, "_learn_template", lambda *a, **k: None)

        result = ReviewClusterService.batch_confirm_cluster(db, base.id, [t1.id])
        assert result["success_count"] == 0
        assert result["failed_count"] == 1
        assert "磁盘满" in result["results"][0]["error"]
