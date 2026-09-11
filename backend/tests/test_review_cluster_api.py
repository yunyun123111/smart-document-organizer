"""待审核指纹聚类端点 API 测试（V5 步骤）。

覆盖：
- GET /api/review/clusters：空库返回 []、同指纹待审核聚为 1 簇、不被 /{doc_id} 捕获
- POST /api/review/batch-confirm-cluster：批量确认归档（真实 ArchiveService）、
  继承基准分类、状态变更、统计结果
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.config import settings
from backend.main import app
from backend.models import (
    STATUS_ARCHIVED,
    STATUS_NEED_REVIEW,
    Document,
    DocumentFingerprint,
    DocumentField,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立临时库 + 独立目录 + 不持久化配置。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    with TestClient(app) as c:
        yield c


def _add_doc(db: Session, name: str, root, status: str = STATUS_NEED_REVIEW) -> Document:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(f"content-{name}".encode("utf-8"))
    doc = Document(
        original_filename=name,
        current_filename=name,
        original_path=str(p),
        current_path=str(p),
        file_type="pdf",
        document_type="销售合同",
        status=status,
        confidence=0.9,
    )
    db.add(doc)
    db.flush()
    db.add(
        DocumentField(
            document_id=doc.id, field_name="suggested_category",
            field_value="合同/销售合同", confidence=1.0,
        )
    )
    db.add(
        DocumentField(
            document_id=doc.id, field_name="date", field_value="2026-08-14",
            confidence=1.0,
        )
    )
    db.add(
        DocumentField(
            document_id=doc.id, field_name="contract_no",
            field_value="SJWLXS-2026-YC0001", confidence=1.0,
        )
    )
    db.commit()
    return doc


def _add_fingerprint(db: Session, doc: Document, layout: str, simhash: str) -> None:
    db.add(
        DocumentFingerprint(
            document_id=doc.id, layout_hash=layout, text_simhash=simhash,
            feature_meta={"source": "text_layer", "block_count": 3},
        )
    )
    db.commit()


class TestReviewClustersApi:
    def test_clusters_empty(self, client):
        r = client.get("/api/review/clusters")
        assert r.status_code == 200
        assert r.json() == []

    def test_clusters_groups_same_fingerprint(self, client, db, tmp_path):
        d1 = _add_doc(db, "a.pdf", tmp_path)
        d2 = _add_doc(db, "b.pdf", tmp_path)
        _add_fingerprint(db, d1, "L1", "1111111111111111")
        _add_fingerprint(db, d2, "L1", "1111111111111111")

        r = client.get("/api/review/clusters")
        assert r.status_code == 200
        clusters = r.json()
        assert len(clusters) == 1
        assert clusters[0]["count"] == 2
        # 端点未被 /{doc_id} 捕获（返回的是 list 而非单文档 dict）
        assert isinstance(clusters, list)
        assert "base_fingerprint" not in clusters[0]

    def test_clusters_split_different_fingerprint(self, client, db, tmp_path):
        d1 = _add_doc(db, "a.pdf", tmp_path)
        d2 = _add_doc(db, "b.pdf", tmp_path)
        _add_fingerprint(db, d1, "L1", "1111111111111111")
        _add_fingerprint(db, d2, "L2", "ffffffffffffffff")

        r = client.get("/api/review/clusters")
        assert len(r.json()) == 2


class TestBatchConfirmClusterApi:
    def test_batch_confirm_cluster_success(self, client, db, tmp_path):
        base = _add_doc(db, "base.pdf", tmp_path)
        t1 = _add_doc(db, "t1.pdf", tmp_path)
        t2 = _add_doc(db, "t2.pdf", tmp_path)

        r = client.post(
            "/api/review/batch-confirm-cluster",
            json={
                "base_document_id": base.id,
                "target_document_ids": [t1.id, t2.id],
                "save_as_sample": True,
            },
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["success_count"] == 2
        assert data["failed_count"] == 0
        assert data["total"] == 2

        # 目标文档已归档，分类继承基准（合同/销售合同）
        db.expire_all()  # 请求在独立 Session 提交，刷新本会话避免读到旧缓存
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

    def test_batch_confirm_with_category_override(self, client, db, tmp_path):
        base = _add_doc(db, "base.pdf", tmp_path)
        t1 = _add_doc(db, "t1.pdf", tmp_path)

        r = client.post(
            "/api/review/batch-confirm-cluster",
            json={
                "base_document_id": base.id,
                "target_document_ids": [t1.id],
                "category_path": "财务/发票",
            },
        )
        assert r.json()["data"]["success_count"] == 1
        db.expire_all()
        field = (
            db.query(DocumentField)
            .filter(
                DocumentField.document_id == t1.id,
                DocumentField.field_name == "suggested_category",
            )
            .first()
        )
        assert field.field_value == "财务/发票"

    def test_batch_confirm_missing_base_returns_404(self, client, db, tmp_path):
        r = client.post(
            "/api/review/batch-confirm-cluster",
            json={"base_document_id": 9999, "target_document_ids": []},
        )
        assert r.status_code == 404
        assert "基准文档不存在" in r.json()["detail"]
