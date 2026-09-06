"""V2.0 业务档案模块测试：迁移 V4、模型约束、级联删除、关联操作。"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.database import SCHEMA_VERSION, _migrate_create_business_archive
from fastapi.testclient import TestClient
from backend.main import app
from backend.models import (
    BUSINESS_STATUS_ACTIVE,
    BusinessFile,
    BusinessRecord,
    Document,
    DocumentField,
    FILE_ROLE_CONTRACT,
    FILE_ROLE_SETTLEMENT,
    LINK_SOURCE_EXCEL_LEDGER,
)


def _make_doc(db, name: str = "测试合同.pdf") -> Document:
    doc = Document(original_filename=name, original_path=f"/tmp/{name}")
    db.add(doc)
    db.flush()
    return doc


class TestMigrationV4:
    """迁移函数：建表 / 索引 / 幂等 / 迁移记录。"""

    def test_schema_version_is_4(self):
        assert SCHEMA_VERSION == 4

    def test_migration_creates_tables_and_indexes(self, db):
        _migrate_create_business_archive(db)
        tables = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('business_records','business_files')"
                )
            ).fetchall()
        }
        assert tables == {"business_records", "business_files"}
        idx = {r[0] for r in db.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()}
        assert "ux_business_records_business_no" in idx
        assert "ix_business_records_status" in idx
        assert "ix_business_records_ship_name" in idx
        assert "ux_business_files_business_document" in idx
        assert "ix_business_files_document_id" in idx
        assert "ix_business_files_business_id" in idx

    def test_migration_idempotent(self, db):
        _migrate_create_business_archive(db)
        _migrate_create_business_archive(db)  # 二次执行不报错

    def test_migration_records_version(self, db):
        _migrate_create_business_archive(db)
        rows = db.execute(
            text("SELECT version, name FROM schema_migrations WHERE version=4")
        ).fetchall()
        assert rows and rows[0][1] == "create business_archive"


class TestBusinessRecordModel:
    """业务档案主表：创建 / 唯一约束 / 默认值。"""

    def test_create_record(self, db):
        _migrate_create_business_archive(db)
        rec = BusinessRecord(
            business_no="YC0453",
            title="YC0453 销售业务",
            ship_name="维克",
            counterparty="青岛中资钢联国际贸易有限公司",
            total_amount=3417350,
        )
        db.add(rec)
        db.commit()
        assert rec.id is not None
        assert rec.status == BUSINESS_STATUS_ACTIVE
        assert rec.extra_data == "{}"

    def test_business_no_unique(self, db):
        _migrate_create_business_archive(db)
        db.add(BusinessRecord(business_no="YC0453"))
        db.flush()
        db.add(BusinessRecord(business_no="YC0453"))
        with pytest.raises(IntegrityError):
            db.flush()


class TestBusinessFileModel:
    """档案-文件关联表：创建 / 唯一约束 / 级联删除。"""

    def test_link_document_to_business(self, db):
        _migrate_create_business_archive(db)
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        bf = BusinessFile(
            business_id=rec.id,
            document_id=doc.id,
            file_role=FILE_ROLE_CONTRACT,
            is_primary=True,
            link_source=LINK_SOURCE_EXCEL_LEDGER,
        )
        db.add(bf)
        db.commit()
        assert bf.id is not None
        assert bf.file_role == FILE_ROLE_CONTRACT
        assert bf.is_primary is True

    def test_unique_pair(self, db):
        """同一 (business_id, document_id) 不能重复挂载。"""
        _migrate_create_business_archive(db)
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        db.add(BusinessFile(business_id=rec.id, document_id=doc.id))
        db.flush()
        db.add(BusinessFile(business_id=rec.id, document_id=doc.id))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_same_document_in_different_business_ok(self, db):
        """同一文件可以挂到不同档案（唯一约束仅限同一档案内）。"""
        _migrate_create_business_archive(db)
        r1 = BusinessRecord(business_no="YC0453")
        r2 = BusinessRecord(business_no="YC0454")
        db.add_all([r1, r2])
        db.flush()
        doc = _make_doc(db)
        db.add(BusinessFile(business_id=r1.id, document_id=doc.id))
        db.add(BusinessFile(business_id=r2.id, document_id=doc.id))
        db.commit()  # 不抛错

    def test_cascade_delete_business(self, db):
        """删除档案 → business_files 级联删除（数据库级 ON DELETE CASCADE）。"""
        _migrate_create_business_archive(db)
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        db.add(BusinessFile(business_id=rec.id, document_id=doc.id, file_role=FILE_ROLE_SETTLEMENT))
        db.commit()
        rec_id = rec.id
        db.delete(rec)
        db.commit()
        n = db.execute(
            text("SELECT COUNT(*) FROM business_files WHERE business_id=:b"), {"b": rec_id}
        ).scalar()
        assert n == 0

    def test_cascade_delete_document(self, db):
        """删除 documents 记录 → business_files 级联删除。"""
        _migrate_create_business_archive(db)
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        doc_id = doc.id
        db.add(BusinessFile(business_id=rec.id, document_id=doc_id))
        db.commit()
        db.execute(text("DELETE FROM documents WHERE id=:d"), {"d": doc_id})
        db.commit()
        n = db.execute(
            text("SELECT COUNT(*) FROM business_files WHERE document_id=:d"), {"d": doc_id}
        ).scalar()
        assert n == 0

    def test_orm_relationship(self, db):
        """ORM 双向关系：rec.files 可加载关联文件。"""
        _migrate_create_business_archive(db)
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        db.add(BusinessFile(business_id=rec.id, document_id=doc.id, file_role=FILE_ROLE_CONTRACT))
        db.commit()
        assert len(rec.files) == 1
        assert rec.files[0].file_role == FILE_ROLE_CONTRACT
        assert rec.files[0].business is rec


# ==================== API 测试（V2.0 业务档案） ====================


@pytest.fixture()
def client():
    """业务档案 API 测试客户端（使用 conftest 隔离的临时测试库）。"""
    with TestClient(app) as c:
        yield c


class TestBusinessArchiveAPI:
    def test_create_archive(self, client):
        r = client.post(
            "/api/business-archives",
            json={
                "business_no": "YC0453",
                "title": "测试业务",
                "status": "active",
                "ship_name": "维克",
                "total_amount": 1000.5,
            },
        )
        assert r.status_code == 201
        data = r.json()["data"]
        assert data["business_no"] == "YC0453"
        assert data["ship_name"] == "维克"
        assert data["total_amount"] == 1000.5
        assert data["status"] == "active"

    def test_create_duplicate_conflict(self, client):
        assert client.post("/api/business-archives", json={"business_no": "YC0453"}).status_code == 201
        assert client.post("/api/business-archives", json={"business_no": "YC0453"}).status_code == 409

    def test_create_invalid_status(self, client):
        r = client.post("/api/business-archives", json={"business_no": "X1", "status": "bad"})
        assert r.status_code == 400

    def test_list_archives(self, client):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        client.post("/api/business-archives", json={"business_no": "YC0454", "status": "completed"})
        r = client.get("/api/business-archives")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_list_filter_status(self, client):
        client.post("/api/business-archives", json={"business_no": "YC0453", "status": "active"})
        client.post("/api/business-archives", json={"business_no": "YC0454", "status": "completed"})
        r = client.get("/api/business-archives", params={"status": "completed"})
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["business_no"] == "YC0454"

    def test_list_keyword(self, client):
        client.post("/api/business-archives", json={"business_no": "YC0453", "ship_name": "维克"})
        client.post("/api/business-archives", json={"business_no": "YC0454", "ship_name": "嘉翔达"})
        r = client.get("/api/business-archives", params={"keyword": "维克"})
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["business_no"] == "YC0453"

    def test_list_pagination(self, client):
        for i in range(5):
            client.post("/api/business-archives", json={"business_no": f"YC{i:04d}"})
        r = client.get("/api/business-archives", params={"skip": 1, "limit": 2})
        body = r.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2

    def test_get_detail_with_files(self, client, db):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        rec = db.execute(select(BusinessRecord).where(BusinessRecord.business_no == "YC0453")).scalar_one()
        doc = Document(
            original_filename="结算单.pdf", original_path="/tmp/s.pdf",
            document_type="结算单", status="archived",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        # 手动挂载（file_role 缺省 → 按文档类型自动映射为 settlement）
        r = client.post(f"/api/business-archives/{rec.id}/files", json={"document_id": doc.id})
        assert r.status_code == 201
        data = r.json()["data"]
        assert data["file_count"] == 1
        assert data["files"][0]["document_filename"] == "结算单.pdf"
        assert data["files"][0]["file_role"] == "settlement"

    def test_get_archive_404(self, client):
        assert client.get("/api/business-archives/99999").status_code == 404

    def test_update_archive(self, client):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        rid = client.get("/api/business-archives", params={"keyword": "YC0453"}).json()["items"][0]["id"]
        r = client.put(f"/api/business-archives/{rid}", json={"title": "新标题", "status": "completed"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["title"] == "新标题"
        assert data["status"] == "completed"

    def test_update_invalid_status(self, client):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        rid = client.get("/api/business-archives", params={"keyword": "YC0453"}).json()["items"][0]["id"]
        r = client.put(f"/api/business-archives/{rid}", json={"status": "bad"})
        assert r.status_code == 400

    def test_add_file_document_missing(self, client):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        rid = client.get("/api/business-archives", params={"keyword": "YC0453"}).json()["items"][0]["id"]
        r = client.post(f"/api/business-archives/{rid}/files", json={"document_id": 99999})
        assert r.status_code == 404

    def test_remove_file(self, client, db):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        rec = db.execute(select(BusinessRecord)).scalar_one()
        doc = Document(
            original_filename="发票.pdf", original_path="/tmp/i.pdf",
            document_type="发票", status="archived",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        client.post(f"/api/business-archives/{rec.id}/files", json={"document_id": doc.id})
        r = client.delete(f"/api/business-archives/{rec.id}/files/{doc.id}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # 解绑不删除物理文档记录
        assert db.get(Document, doc.id) is not None

    def test_remove_file_404(self, client, db):
        client.post("/api/business-archives", json={"business_no": "YC0453"})
        rec = db.execute(select(BusinessRecord)).scalar_one()
        r = client.delete(f"/api/business-archives/{rec.id}/files/99999")
        assert r.status_code == 404

    def test_backfill_end_to_end(self, client, db):
        """回填：销售合同 + 结算单（同 contract_no）→ 同一档案、2 条关联。"""
        c = Document(
            original_filename="合同.pdf", original_path="/tmp/c.pdf",
            document_type="销售合同", status="archived",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        db.add(DocumentField(
            document_id=c.id, field_name="contract_no",
            field_value="SJWLXS（DD）-2026-YC0452", confidence=0.99,
        ))
        s = Document(
            original_filename="结算单.pdf", original_path="/tmp/s.pdf",
            document_type="结算单", status="archived",
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        db.add(DocumentField(
            document_id=s.id, field_name="contract_no",
            field_value="SJWLXS（DD）-2026-YC0452", confidence=0.99,
        ))
        db.commit()

        r = client.post("/api/business-archives/backfill")
        assert r.status_code == 200
        stats = r.json()["stats"]
        assert stats["scanned"] == 2
        assert stats["linked"] == 2
        assert stats["created_records"] == 1
        assert stats["failed"] == 0

        # 结算单跟合同走到同一档案，file_count=2
        body = client.get("/api/business-archives").json()
        assert body["total"] == 1
        assert body["items"][0]["business_no"] == "SJWLXS（DD）-2026-YC0452"
        assert body["items"][0]["file_count"] == 2
