"""识别质量看板（/api/system/dashboard/review-quality）回归测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import (
    DECISION_APPROVE,
    DECISION_SKIP,
    DocumentSample,
    ReviewDecision,
    RuleCandidate,
)


def _decision(db, **kw):
    d = ReviewDecision(
        document_id=kw.get("document_id", 1),
        action=kw.get("action", DECISION_APPROVE),
        source="single",
        confidence=kw.get("confidence"),
        category_changed=kw.get("category_changed", False),
        type_changed=kw.get("type_changed", False),
        final_category=kw.get("final_category", "合同/销售合同"),
    )
    db.add(d)
    return d


def _get():
    with TestClient(app) as c:
        r = c.get("/api/system/dashboard/review-quality")
        assert r.status_code == 200, r.text
        return r.json()


def test_empty_database(db):
    body = _get()
    assert body["total_decisions"] == 0
    assert body["approved"] == 0
    assert body["category_change_rate"] == 0.0
    assert body["samples_total"] == 0
    assert body["pending_candidates"] == 0
    assert len(body["confidence_buckets"]) == 4


def test_change_rate_and_counts(db):
    # 4 次确认，其中 2 次改判；另 1 次跳过
    _decision(db, category_changed=True, confidence=0.4)
    _decision(db, category_changed=True, confidence=0.65)
    _decision(db, category_changed=False, confidence=0.9)
    _decision(db, category_changed=False, confidence=0.75, type_changed=True)
    _decision(db, action=DECISION_SKIP, confidence=0.3, final_category="合同/销售合同")
    db.add(DocumentSample(document_type="销售合同", category_path="合同/销售合同",
                          fingerprint="{}", note="由审核确认自动学习（文档 #1）"))
    db.add(RuleCandidate(keyword="HSYCXS", category_path="合同/采购合同"))
    db.commit()

    body = _get()
    assert body["total_decisions"] == 5
    assert body["approved"] == 4
    assert body["skipped"] == 1
    assert body["category_changed"] == 2
    assert body["category_change_rate"] == 50.0
    assert body["type_changed"] == 1
    assert body["samples_total"] == 1
    assert body["samples_auto"] == 1
    assert body["pending_candidates"] == 1

    buckets = {b["label"]: b for b in body["confidence_buckets"]}
    assert buckets["低 (<0.5)"]["count"] == 2     # 0.4、0.3(skip)
    assert buckets["低 (<0.5)"]["changed"] == 1  # 只有 0.4 那次改判
    assert buckets["中低 (0.5~0.7)"]["count"] == 1
    assert buckets["中高 (0.7~0.85)"]["count"] == 1
    assert buckets["高 (≥0.85)"]["count"] == 1
