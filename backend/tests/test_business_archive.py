"""V2.0 业务档案模块测试：迁移 V4、模型约束、级联删除、关联操作。"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.database import SCHEMA_VERSION, _migrate_create_business_archive
from backend.models import (
    BUSINESS_STATUS_ACTIVE,
    BusinessFile,
    BusinessRecord,
    Document,
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
