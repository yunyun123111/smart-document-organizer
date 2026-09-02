# -*- coding: utf-8 -*-
"""Dashboard 趋势与异常提醒测试。"""
from datetime import datetime, timedelta

import pytest

from backend.models import (
    STATUS_ARCHIVED,
    STATUS_FAILED,
    STATUS_NEED_REVIEW,
    STATUS_PROCESSED,
    Document,
    OperationLog,
)
from backend.services.dashboard_service import (
    build_daily,
    build_ocr_daily,
    detect_alerts,
    get_dashboard_trends,
)


def _mk_doc(db, days_ago, doc_type="发票", status=STATUS_ARCHIVED):
    d = Document(
        original_filename="x.pdf", current_filename="x.pdf",
        original_path="/x", current_path="/x", document_type=doc_type,
        status=status, created_at=datetime.now() - timedelta(days=days_ago),
    )
    db.add(d)
    return d


def _mk_ocr_log(db, days_ago, result="ok"):
    op = OperationLog(
        operation_type="OCR", result=result,
        created_at=datetime.now() - timedelta(days=days_ago),
    )
    db.add(op)
    return op


def test_daily_trend_basic(db):
    _mk_doc(db, 0, status=STATUS_ARCHIVED)
    _mk_doc(db, 0, status=STATUS_NEED_REVIEW)
    _mk_doc(db, 0, status=STATUS_FAILED)
    _mk_doc(db, 3, status=STATUS_ARCHIVED)
    db.commit()

    daily = build_daily(db, days=7)
    assert len(daily) == 7
    today = daily[-1]
    assert today["new"] == 3
    assert today["archived"] == 1
    assert today["need_review"] == 1
    assert today["failed"] == 1
    assert today["success_rate"] == pytest.approx(2 / 3, abs=0.001)  # (archived+need_review)/new
    day3 = daily[3]  # 今天-3 天
    assert day3["new"] == 1
    assert day3["success_rate"] == 1.0


def test_daily_missing_dates_zero(db):
    _mk_doc(db, 0)
    db.commit()
    daily = build_daily(db, days=14)
    assert len(daily) == 14
    assert daily[0]["new"] == 0
    assert daily[0]["success_rate"] is None


def test_ocr_daily(db):
    _mk_ocr_log(db, 0, "ok")
    _mk_ocr_log(db, 0, "ok")
    _mk_ocr_log(db, 0, "failed")
    db.commit()
    ocr = build_ocr_daily(db, days=7)
    today = ocr[-1]
    assert today["total"] == 3
    assert today["failed"] == 1
    assert today["fail_rate"] == pytest.approx(1 / 3, abs=0.001)


def test_type_surge_alert(db):
    # 前 7 天 1 份，近 7 天 6 份 → 暴增
    _mk_doc(db, 10, doc_type="合同")
    for i in range(6):
        _mk_doc(db, i, doc_type="合同")
    db.commit()
    alerts = detect_alerts(db)
    surge = [a for a in alerts if a["kind"] == "type_surge"]
    assert any(a["type"] == "合同" for a in surge)


def test_type_no_surge_when_steady(db):
    # 近 7 天 4 份、前 7 天 4 份 → 不触发（<5 或不足 3 倍）
    for i in range(4):
        _mk_doc(db, i, doc_type="发票")
    for i in range(7, 11):
        _mk_doc(db, i, doc_type="发票")
    db.commit()
    alerts = detect_alerts(db)
    assert not any(a["kind"] == "type_surge" for a in alerts)


def test_ocr_fail_alert(db):
    for i in range(6):
        _mk_ocr_log(db, i, "failed")
    for i in range(4):
        _mk_ocr_log(db, i, "ok")
    db.commit()
    alerts = detect_alerts(db)
    assert any(a["kind"] == "ocr_fail" for a in alerts)


def test_full_trends(db):
    _mk_doc(db, 0, status=STATUS_PROCESSED)
    _mk_ocr_log(db, 0, "ok")
    db.commit()
    data = get_dashboard_trends(db, days=7)
    assert len(data["daily"]) == 7
    assert len(data["ocr_daily"]) == 7
    assert isinstance(data["alerts"], list)
