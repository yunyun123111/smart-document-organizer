"""单条重算与全量重算必须给出同一套绑定结果。

历史缺陷：只拿一条业务线去推断归属时，「唯一命中」会让同一客户的别家单据
被算到这一条线上 —— 用户点「补单」触发单条重算会看到有资料，下次全量重算又
变回「未上传资料」，闭环判定跟着一起漂。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import DocumentField
from backend.tests.test_reconciliation_api import _add_doc, _register, ledger  # noqa: F401


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _matrix_total(detail: dict) -> int:
    return sum(int(cell["count"]) for cell in detail["matrix"])


def test_single_run_does_not_steal_sibling_documents(client, db, ledger):
    """同一客户的两条业务线 + 一份只能按公司名认领的发票。

    全量：无法唯一归属 → 进待归位池，两条线都不算"有资料"。
    单条：也必须一致，不能因为只剩一条线就把它硬认过来。
    """
    _register(db, ledger)
    doc = _add_doc(db, "客户发票.pdf", "发票")
    db.add(DocumentField(document_id=doc.id, field_name="company", field_value="下游客户"))
    db.commit()

    client.post("/api/reconciliation/run", json={})
    full = client.get("/api/reconciliation/reports/YWX101").json()
    assert _matrix_total(full) == 0
    assert full["closure"]["stage"] == "not_started"

    client.post("/api/reconciliation/run", json={"business_no": "YWX101"})
    single = client.get("/api/reconciliation/reports/YWX101").json()
    assert _matrix_total(single) == 0
    assert single["closure"]["stage"] == "not_started"


def test_single_run_keeps_own_documents(client, db, ledger):
    """真正带本线合同号的单据，单条重算照样归得上（不能为了防抢而全丢）。"""
    _register(db, ledger)
    _add_doc(db, "上游发票.pdf", "发票", contract_no="HSYCXS-101", amount="1020000")

    client.post("/api/reconciliation/run", json={"business_no": "YWX101"})
    detail = client.get("/api/reconciliation/reports/YWX101").json()
    assert _matrix_total(detail) > 0
