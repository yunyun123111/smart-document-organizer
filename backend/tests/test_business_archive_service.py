"""业务档案服务测试：自动归集 / 手动绑定 / 解除关联 / 历史回填。"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.models import (
    BUSINESS_STATUS_ACTIVE,
    FILE_ROLE_CONTRACT,
    FILE_ROLE_INVOICE,
    FILE_ROLE_OTHER,
    FILE_ROLE_SETTLEMENT,
    BusinessFile,
    BusinessRecord,
    Document,
    DocumentField,
    OP_BUSINESS_AUTO_LINK,
    OP_BUSINESS_MANUAL_LINK,
    OP_BUSINESS_UNLINK,
    STATUS_RECYCLED,
)
from backend.services.business_archive_service import (
    BusinessArchiveError,
    BusinessArchiveService,
)

svc = BusinessArchiveService()


def _make_doc(db, name: str = "doc.pdf", doc_type: str = "结算单") -> Document:
    doc = Document(
        original_filename=name,
        original_path=f"/tmp/{name}",
        current_path=f"/tmp/{name}",
        file_type="pdf",
        document_type=doc_type,
        title=name,
        status="archived",
    )
    db.add(doc)
    db.flush()
    return doc


def _add_field(db, doc_id: int, name: str, value: str) -> None:
    db.add(DocumentField(document_id=doc_id, field_name=name, field_value=value, confidence=0.99))
    db.flush()


def _count(db, model) -> int:
    return len(db.execute(select(model)).scalars().all())


class TestAutoLink:
    def test_no_contract_skips(self, db):
        doc = _make_doc(db)
        result = svc.auto_link_document(db, doc.id)
        assert result is None
        assert _count(db, BusinessRecord) == 0
        assert _count(db, BusinessFile) == 0

    def test_document_not_found(self, db):
        assert svc.auto_link_document(db, 99999) is None

    def test_new_business_created_and_linked(self, db):
        doc = _make_doc(db, doc_type="销售合同")
        _add_field(db, doc.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        _add_field(db, doc.id, "vessel", "维克")
        _add_field(db, doc.id, "amount", "3,417,350.00")
        _add_field(db, doc.id, "date", "2026-08-13")

        rec = svc.auto_link_document(db, doc.id)
        assert rec is not None
        assert rec.business_no == "SJWLXS（DD）-2026-YC0452"
        assert rec.status == BUSINESS_STATUS_ACTIVE
        assert rec.ship_name == "维克"
        assert str(rec.total_amount) == "3417350.00"
        assert rec.sign_date is not None and rec.sign_date.year == 2026

        link = db.execute(select(BusinessFile)).scalar_one()
        assert link.business_id == rec.id
        assert link.document_id == doc.id
        assert link.file_role == FILE_ROLE_CONTRACT
        assert link.link_source == "auto_rule"

    def test_settlement_follows_existing_business(self, db):
        """结算单与销售合同共享 contract_no → 挂到同一档案。"""
        contract = _make_doc(db, name="合同.pdf", doc_type="销售合同")
        _add_field(db, contract.id, "contract_no", "SJWLXS（DD）-2026-YC0429")
        rec = svc.auto_link_document(db, contract.id)
        assert rec is not None

        settlement = _make_doc(db, name="结算单.pdf", doc_type="结算单")
        _add_field(db, settlement.id, "contract_no", "SJWLXS（DD）-2026-YC0429")
        rec2 = svc.auto_link_document(db, settlement.id)
        assert rec2 is not None and rec2.id == rec.id
        assert _count(db, BusinessRecord) == 1  # 不新建档案
        assert _count(db, BusinessFile) == 2

        link = db.execute(
            select(BusinessFile).where(BusinessFile.document_id == settlement.id)
        ).scalar_one()
        assert link.file_role == FILE_ROLE_SETTLEMENT

    def test_invoice_role_mapping(self, db):
        doc = _make_doc(db, doc_type="发票")
        _add_field(db, doc.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        rec = svc.auto_link_document(db, doc.id)
        assert rec is not None
        link = db.execute(select(BusinessFile)).scalar_one()
        assert link.file_role == FILE_ROLE_INVOICE

    def test_unknown_type_role_is_other(self, db):
        doc = _make_doc(db, doc_type="未知类型")
        _add_field(db, doc.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        svc.auto_link_document(db, doc.id)
        link = db.execute(select(BusinessFile)).scalar_one()
        assert link.file_role == FILE_ROLE_OTHER

    def test_idempotent(self, db):
        doc = _make_doc(db, doc_type="销售合同")
        _add_field(db, doc.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        r1 = svc.auto_link_document(db, doc.id)
        r2 = svc.auto_link_document(db, doc.id)
        assert r1 is not None and r2 is not None and r1.id == r2.id
        assert _count(db, BusinessFile) == 1  # 不重复关联


class TestManualLink:
    def test_manual_link_ok(self, db):
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db, doc_type="发票")
        link = svc.manual_link_file(db, rec.id, doc.id, FILE_ROLE_INVOICE)
        assert link.file_role == FILE_ROLE_INVOICE
        assert link.link_source == "manual"

    def test_duplicate_link_raises(self, db):
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        svc.manual_link_file(db, rec.id, doc.id, FILE_ROLE_OTHER)
        with pytest.raises(BusinessArchiveError):
            svc.manual_link_file(db, rec.id, doc.id, FILE_ROLE_OTHER)

    def test_business_missing_raises(self, db):
        doc = _make_doc(db)
        with pytest.raises(BusinessArchiveError):
            svc.manual_link_file(db, 99999, doc.id, FILE_ROLE_OTHER)

    def test_document_missing_raises(self, db):
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        with pytest.raises(BusinessArchiveError):
            svc.manual_link_file(db, rec.id, 99999, FILE_ROLE_OTHER)


class TestUnlink:
    def test_unlink_ok(self, db):
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        svc.manual_link_file(db, rec.id, doc.id, FILE_ROLE_CONTRACT)
        assert svc.unlink_file(db, rec.id, doc.id) is True
        assert _count(db, BusinessFile) == 0
        # 物理文档记录不受影响
        assert db.get(Document, doc.id) is not None

    def test_unlink_missing_link_returns_false(self, db):
        rec = BusinessRecord(business_no="YC0453")
        db.add(rec)
        db.flush()
        doc = _make_doc(db)
        assert svc.unlink_file(db, rec.id, doc.id) is False


class TestBackfill:
    def test_backfill_stats(self, db):
        # 1) 销售合同（有 contract_no）→ 新建档案
        c = _make_doc(db, name="合同.pdf", doc_type="销售合同")
        _add_field(db, c.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        # 2) 结算单（同 contract_no）→ 复用档案
        s = _make_doc(db, name="结算单.pdf", doc_type="结算单")
        _add_field(db, s.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        # 3) 发票（无 contract_no）→ 跳过
        i = _make_doc(db, name="发票.pdf", doc_type="发票")

        stats = svc.backfill_historical_data(db)
        assert stats["scanned"] == 3
        assert stats["linked"] == 2
        assert stats["skipped_no_contract"] == 1
        assert stats["failed"] == 0
        assert stats["created_records"] == 1
        assert _count(db, BusinessRecord) == 1
        assert _count(db, BusinessFile) == 2

    def test_backfill_skips_recycled(self, db):
        doc = _make_doc(db, doc_type="销售合同")
        _add_field(db, doc.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        doc.status = STATUS_RECYCLED
        db.flush()
        stats = svc.backfill_historical_data(db)
        assert stats["scanned"] == 0
        assert _count(db, BusinessRecord) == 0

    def test_backfill_already_linked(self, db):
        c = _make_doc(db, doc_type="销售合同")
        _add_field(db, c.id, "contract_no", "SJWLXS（DD）-2026-YC0452")
        svc.auto_link_document(db, c.id)
        stats = svc.backfill_historical_data(db)
        assert stats["scanned"] == 1
        assert stats["already_linked"] == 1
        assert stats["linked"] == 0
        assert stats["created_records"] == 0
