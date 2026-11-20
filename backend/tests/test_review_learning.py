"""P1 审核留痕与样本沉淀回归测试。

覆盖：
- 单条确认：改分类 / 改类型后，review_decisions 里保留 AI 原始建议（不被覆盖）
- 跳过：记录 action=skip
- 首次确认 → 沉淀格式样本；同类型同分类再确认 → 不重复建
- LEARN_SAMPLE_ON_APPROVE=False → 不建样本
- 批量确认：每条都留痕
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import (
    STATUS_NEED_REVIEW,
    Document,
    DocumentField,
    DocumentSample,
    ReviewDecision,
)

# 足够长的单据正文，能形成有效指纹（labels/grams 非空）
CONTRACT_TEXT = (
    "销售合同\n"
    "合同编号：SJWLXS-2026-YC0001\n"
    "甲方：ABC有限公司\n"
    "乙方：DEF有限公司\n"
    "合同金额：人民币 128,500.00 元\n"
    "签订日期：2026年08月20日\n"
    "货物名称：铁矿粉 湿吨 车板交货\n"
    "本合同由甲乙双方根据《中华人民共和国民法典》订立，双方应严格履行。\n"
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    with TestClient(app) as c:
        yield c


def _add_doc(
    db: Session,
    root,
    *,
    name: str = "doc.pdf",
    doc_type: str = "销售合同",
    category: str = "合同/销售合同",
    text: str = CONTRACT_TEXT,
    contract_no: str = "SJWLXS-2026-YC0001",
) -> Document:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    # 内容必须互不相同：归档前按 file_hash 判重，内容一样会被当成重复文件而拒绝归档
    p.write_bytes(f"%PDF-1.4 fake content of {name}".encode("utf-8"))
    doc = Document(
        original_filename=name,
        current_filename=name,
        original_path=str(p),
        current_path=str(p),
        file_type="pdf",
        document_type=doc_type,
        status=STATUS_NEED_REVIEW,
        confidence=0.72,
        extracted_text=text,
    )
    db.add(doc)
    db.flush()
    db.add(
        DocumentField(
            document_id=doc.id, field_name="suggested_category",
            field_value=category, confidence=1.0,
        )
    )
    db.add(
        DocumentField(
            document_id=doc.id, field_name="contract_no",
            field_value=contract_no, confidence=1.0,
        )
    )
    db.commit()
    return doc


def _decisions(doc_id: int) -> list[ReviewDecision]:
    with SessionLocal() as s:
        return list(
            s.query(ReviewDecision).filter(ReviewDecision.document_id == doc_id).all()
        )


class TestDecisionRecording:
    def test_approve_keeps_ai_suggestion_when_changed(self, client, db, tmp_path):
        """人工把分类从 合同/销售合同 改成 合同/采购合同 → 留痕保留原建议。"""
        doc = _add_doc(db, tmp_path)
        doc_id = doc.id

        r = client.post(
            f"/api/review/{doc_id}/approve",
            json={"category_path": "合同/采购合同"},
        )
        assert r.status_code == 200, r.text

        rows = _decisions(doc_id)
        assert len(rows) == 1
        d = rows[0]
        assert d.suggested_category == "合同/销售合同"
        assert d.final_category == "合同/采购合同"
        assert d.category_changed is True
        assert d.action == "approve"
        assert d.source == "single"
        assert d.confidence == 0.72

    def test_approve_type_change_recorded(self, client, db, tmp_path):
        doc = _add_doc(db, tmp_path, doc_type="其他")
        doc_id = doc.id

        r = client.post(
            f"/api/review/{doc_id}/approve",
            json={"document_type": "采购合同", "category_path": "合同/采购合同"},
        )
        assert r.status_code == 200, r.text

        d = _decisions(doc_id)[0]
        assert d.suggested_document_type == "其他"
        assert d.final_document_type == "采购合同"
        assert d.type_changed is True

    def test_skip_recorded(self, client, db, tmp_path):
        doc = _add_doc(db, tmp_path)
        doc_id = doc.id

        assert client.post(f"/api/review/{doc_id}/skip").status_code == 200
        d = _decisions(doc_id)[0]
        assert d.action == "skip"
        assert d.final_category == "合同/销售合同"

    def test_batch_approve_records_each(self, client, db, tmp_path):
        # 合同号不同 → 建议文件名不同，避免第二份因重名归档失败而不留痕
        ids = [
            _add_doc(db, tmp_path, name=f"b{i}.pdf", contract_no=f"SJWLXS-2026-YC000{i}").id
            for i in range(2)
        ]

        r = client.post("/api/review/batch-approve", json={"doc_ids": ids})
        assert r.status_code == 200, r.text

        with SessionLocal() as s:
            rows = s.query(ReviewDecision).filter(ReviewDecision.document_id.in_(ids)).all()
        assert len(rows) == 2, r.json()
        assert {r.source for r in rows} == {"batch"}


class TestSampleLearning:
    def test_first_approve_creates_sample(self, client, db, tmp_path):
        doc = _add_doc(db, tmp_path)
        doc_id = doc.id

        assert client.post(
            f"/api/review/{doc_id}/approve", json={"category_path": "合同/销售合同"}
        ).status_code == 200

        with SessionLocal() as s:
            samples = s.query(DocumentSample).all()
            assert len(samples) == 1
            assert samples[0].document_type == "销售合同"
            assert samples[0].category_path == "合同/销售合同"
            assert samples[0].sample_doc_id == doc_id
            assert samples[0].fingerprint_dict.get("labels")
        # 留痕里带上样本 id，便于追溯"这条经验来自哪次审核"
        assert _decisions(doc_id)[0].sample_id == samples[0].id

    def test_second_same_type_does_not_duplicate(self, client, db, tmp_path):
        d1 = _add_doc(db, tmp_path, name="a.pdf", contract_no="SJWLXS-2026-YC0001")
        d2 = _add_doc(db, tmp_path, name="b.pdf", contract_no="SJWLXS-2026-YC0002")

        client.post(f"/api/review/{d1.id}/approve", json={"category_path": "合同/销售合同"})
        client.post(f"/api/review/{d2.id}/approve", json={"category_path": "合同/销售合同"})

        with SessionLocal() as s:
            assert s.query(DocumentSample).count() == 1

    def test_short_text_not_learned(self, client, db, tmp_path):
        doc = _add_doc(db, tmp_path, text="结算单")
        doc_id = doc.id

        client.post(f"/api/review/{doc_id}/approve", json={"category_path": "合同/销售合同"})

        with SessionLocal() as s:
            assert s.query(DocumentSample).count() == 0

    def test_switch_off_disables_learning(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "LEARN_SAMPLE_ON_APPROVE", False)
        doc = _add_doc(db, tmp_path)
        doc_id = doc.id

        client.post(f"/api/review/{doc_id}/approve", json={"category_path": "合同/销售合同"})

        with SessionLocal() as s:
            assert s.query(DocumentSample).count() == 0
