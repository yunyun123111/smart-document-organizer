"""Phase 2 数据库层测试：建表、默认分类、各表 CRUD 与约束。"""
from __future__ import annotations

from sqlalchemy import func, inspect, text

from backend.database import DEFAULT_CATEGORY_TREE, engine, seed_default_categories
from backend.models import (
    Category,
    Document,
    DocumentField,
    OperationLog,
    ProcessingJob,
    RenameTemplate,
    Rule,
)


class TestSchema:
    def test_all_tables_created(self):
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {
            "documents",
            "document_fields",
            "categories",
            "rules",
            "rename_templates",
            "processing_jobs",
            "operation_logs",
        }
        assert expected.issubset(tables), f"缺失表: {expected - tables}"

    def test_documents_columns(self):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("documents")}
        expected = {
            "id",
            "original_filename",
            "current_filename",
            "original_path",
            "current_path",
            "file_type",
            "mime_type",
            "file_size",
            "file_hash",
            "document_type",
            "title",
            "confidence",
            "status",
            "extracted_text",
            "created_at",
            "processed_at",
            "updated_at",
        }
        assert expected.issubset(cols), f"缺失字段: {expected - cols}"


class TestDefaultCategories:
    def test_seed_creates_tree(self, db):
        created = seed_default_categories(db)
        total = sum(1 + len(children) for children in DEFAULT_CATEGORY_TREE.values())
        assert created == total

        parents = (
            db.query(Category)
            .filter(Category.parent_id.is_(None))
            .order_by(Category.sort_order)
            .all()
        )
        parent_names = [p.name for p in parents]
        assert parent_names == ["合同", "财务", "业务", "项目资料", "证照", "其他"]

        # 验证层级与 path（子分类集合从种子数据推导，新增分类不破坏本测试）
        contract = next(p for p in parents if p.name == "合同")
        child_names = {c.name for c in contract.children}
        assert child_names == set(DEFAULT_CATEGORY_TREE["合同"])
        sales = next(c for c in contract.children if c.name == "销售合同")
        assert sales.path == "合同/销售合同"
        assert sales.parent_id == contract.id

    def test_seed_idempotent(self, db):
        seed_default_categories(db)
        n1 = db.query(Category).count()
        expected = sum(1 + len(c) for c in DEFAULT_CATEGORY_TREE.values())
        assert n1 == expected
        seed_default_categories(db)
        n2 = db.query(Category).count()
        assert n1 == n2, "重复播种不应新增分类"


class TestCRUD:
    def _make_document(self, db) -> Document:
        doc = Document(
            original_filename="2026合同.pdf",
            current_filename="2026合同.pdf",
            original_path="C:/inbox/2026合同.pdf",
            current_path="C:/inbox/2026合同.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            file_size=1024,
            file_hash="abc123",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc

    def test_document_crud(self, db):
        doc = self._make_document(db)
        assert doc.id is not None
        assert doc.status == "pending"

        # 读取
        fetched = db.get(Document, doc.id)
        assert fetched.original_filename == "2026合同.pdf"

        # 更新
        fetched.document_type = "销售合同"
        fetched.status = "archived"
        db.commit()
        db.refresh(fetched)
        assert fetched.document_type == "销售合同"
        assert fetched.status == "archived"

    def test_document_fields_relation(self, db):
        doc = self._make_document(db)
        doc.fields.append(
            DocumentField(field_name="合同编号", field_value="XS202608001", confidence=0.98, source="AI")
        )
        doc.fields.append(
            DocumentField(field_name="金额", field_value="128500", confidence=0.95, source="RULE")
        )
        db.commit()
        db.refresh(doc)
        assert len(doc.fields) == 2
        assert doc.fields[0].field_name == "合同编号"
        assert doc.fields[0].source == "AI"

    def test_file_hash_indexed_not_unique(self, db):
        """file_hash 是普通索引（允许重复），重复判定由服务层按状态完成。"""
        self._make_document(db)
        dup = Document(
            original_filename="副本.pdf",
            current_filename="副本.pdf",
            original_path="C:/inbox/副本.pdf",
            current_path="C:/inbox/副本.pdf",
            file_type="pdf",
            file_size=1024,
            file_hash="abc123",  # 相同 hash 允许入库（去重逻辑在服务层）
        )
        db.add(dup)
        db.commit()
        count = (
            db.query(func.count(Document.id))
            .filter(Document.file_hash == "abc123")
            .scalar()
        )
        assert count == 2

    def test_category_rule_template(self, db):
        cat = Category(name="销售合同", path="合同/销售合同")
        cat.rules.append(Rule(keyword="销售合同", match_type="contains", priority=10, weight=1.5))
        cat.rename_templates.append(RenameTemplate(template="{日期}_{类型}_{公司}_{编号}"))
        db.add(cat)
        db.commit()
        db.refresh(cat)
        assert len(cat.rules) == 1
        assert len(cat.rename_templates) == 1
        assert cat.rules[0].match_type == "contains"

    def test_processing_job_and_operation_log(self, db):
        job = ProcessingJob(status="running", total_files=3)
        db.add(job)
        db.commit()
        db.refresh(job)

        log = OperationLog(
            document_id=None,
            job_id=job.id,
            operation_type="MOVE",
            old_filename="a.pdf",
            new_filename="b.pdf",
            old_path="C:/a.pdf",
            new_path="C:/b.pdf",
            result="ok",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        assert log.job_id == job.id
        assert log.result == "ok"

    def test_cascade_delete_document_fields(self, db):
        doc = self._make_document(db)
        doc.fields.append(DocumentField(field_name="公司", field_value="ABC", confidence=0.9, source="OCR"))
        db.commit()

        db.delete(doc)
        db.commit()
        count = db.query(DocumentField).count()
        assert count == 0
