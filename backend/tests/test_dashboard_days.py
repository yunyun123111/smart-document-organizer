# -*- coding: utf-8 -*-
"""Dashboard 趋势 days 参数测试：7/30 天范围与 API 透传。"""
from datetime import datetime, timedelta

from backend.models import STATUS_ARCHIVED, Document
from backend.services.dashboard_service import get_dashboard_trends


def _mk_doc(db, days_ago):
    d = Document(
        original_filename="x.pdf", current_filename="x.pdf",
        original_path="/x", current_path="/x", document_type="发票",
        status=STATUS_ARCHIVED, created_at=datetime.now() - timedelta(days=days_ago),
    )
    db.add(d)
    return d


def test_trends_days_range(db):
    _mk_doc(db, 0)
    _mk_doc(db, 5)
    _mk_doc(db, 20)  # 超出 7 天范围，不应计入 7 天趋势
    db.commit()

    t7 = get_dashboard_trends(db, days=7)
    assert len(t7["daily"]) == 7
    assert t7["days"] == 7
    assert t7["daily"][-1]["new"] == 1
    assert t7["daily"][-6]["new"] == 1  # 5 天前
    total7 = sum(x["new"] for x in t7["daily"])
    assert total7 == 2  # 20 天前的文件不计入

    t30 = get_dashboard_trends(db, days=30)
    assert len(t30["daily"]) == 30
    total30 = sum(x["new"] for x in t30["daily"])
    assert total30 == 3
