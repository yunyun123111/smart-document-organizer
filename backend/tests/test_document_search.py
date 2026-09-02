# -*- coding: utf-8 -*-
"""文档库智能搜索测试：拼音 / 错别字容错 / 字段过滤 / 建议。"""
from datetime import datetime

from backend.models import Document, DocumentField
from backend.services.document_search import (
    build_keyword_conditions,
    fuzzy_match_types,
    is_ascii,
    match_field_ids,
    suggest,
)


def _doc(db, filename, doc_type="发票", days=0):
    d = Document(
        original_filename=filename, current_filename=filename,
        original_path="/x", current_path="/x", document_type=doc_type,
        created_at=datetime(2026, 8, 14, 10, 0, 0) if days == 0 else datetime.now(),
    )
    db.add(d)
    db.flush()
    return d


def _field(db, doc, name, value):
    f = DocumentField(document_id=doc.id, field_name=name, field_value=value, confidence=0.9, source="test")
    db.add(f)


def _search_ids(db, keyword=None, **kw):
    from sqlalchemy import or_
    query = db.query(Document.id)
    if keyword:
        query = query.filter(or_(*build_keyword_conditions(db, keyword)))
    return {r[0] for r in query.all()}


# ---------- 拼音搜索 ----------
def test_pinyin_full_match(db):
    a = _doc(db, "发票A.pdf", "发票")
    b = _doc(db, "合同B.pdf", "合同")
    db.commit()
    assert is_ascii("fapiao")
    ids = _search_ids(db, "fapiao")
    assert a.id in ids and b.id not in ids


def test_pinyin_initial_match(db):
    a = _doc(db, "发票A.pdf", "发票")
    b = _doc(db, "合同B.pdf", "合同")
    db.commit()
    ids = _search_ids(db, "fp")
    assert a.id in ids


def test_pinyin_filename_match(db):
    a = _doc(db, "海兴168结算单.pdf", "结算单")
    db.commit()
    ids = _search_ids(db, "haixing")
    assert a.id in ids


# ---------- 错别字容错 ----------
def test_fuzzy_typo_type(db):
    a = _doc(db, "合同A.pdf", "合同")
    b = _doc(db, "发票B.pdf", "发票")
    db.commit()
    ids = _search_ids(db, "合童")  # 错别字
    assert a.id in ids


def test_fuzzy_typo_invoice(db):
    b = _doc(db, "发票B.pdf", "发票")
    db.commit()
    ids = _search_ids(db, "发飘")
    assert b.id in ids


# ---------- 字段过滤 ----------
def test_contract_no_filter(db):
    a = _doc(db, "A.pdf", "销售合同")
    _field(db, a, "contract_no", "XS202608001")
    b = _doc(db, "B.pdf", "发票")
    _field(db, b, "contract_no", "CG202600200")
    db.commit()
    ids = match_field_ids(db, ("contract_no",), like="XS2026")
    assert a.id in ids and b.id not in ids


def test_amount_range_filter(db):
    a = _doc(db, "A.pdf", "发票")
    _field(db, a, "amount", "128500.00")
    b = _doc(db, "B.pdf", "发票")
    _field(db, b, "amount", "5000.00")
    db.commit()
    ids = match_field_ids(db, ("amount",), min_val=10000, max_val=200000)
    assert a.id in ids and b.id not in ids


def test_date_range_filter(db):
    a = _doc(db, "A.pdf", "结算单")
    _field(db, a, "date", "2026-08-14")
    b = _doc(db, "B.pdf", "结算单")
    _field(db, b, "date", "2026-06-01")
    db.commit()
    ids = match_field_ids(db, ("date",), start="2026-08-01", end="2026-08-31")
    assert a.id in ids and b.id not in ids


# ---------- 搜索建议 ----------
def test_suggest_types_and_fields(db):
    a = _doc(db, "发票A.pdf", "发票")
    _field(db, a, "company", "北京能源科技有限公司")
    _field(db, a, "contract_no", "XS202608001")
    b = _doc(db, "合同B.pdf", "合同")
    _field(db, b, "company", "上海贸易有限公司")
    db.commit()
    res = suggest(db, "fapiao")
    assert any(x["type"] == "类型" and x["text"] == "发票" for x in res)
    res2 = suggest(db, "北京能源")
    assert any(x["type"] == "公司" and "北京能源" in x["text"] for x in res2)
    res3 = suggest(db, "XS2026")
    assert any(x["type"] == "合同号" and x["text"] == "XS202608001" for x in res3)


def test_suggest_filename(db):
    _doc(db, "海兴168_结算单.pdf", "结算单")
    db.commit()
    res = suggest(db, "海兴")
    assert any(x["type"] == "文件名" and "海兴" in x["text"] for x in res)
