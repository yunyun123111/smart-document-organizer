"""P2-2：单据进槽位前的体检（错槽提示、方向、对手方、发票号重复）。"""
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


def _doc(db, filename: str, doc_type: str, **fields):
    """造一份已识别单据：contract_no/amount 走 _add_doc，其余字段直接补。"""
    contract_no = fields.pop("contract_no", "")
    amount = fields.pop("amount", "")
    doc = _add_doc(db, filename, doc_type, contract_no=contract_no, amount=amount)
    for name, value in fields.items():
        db.add(DocumentField(document_id=doc.id, field_name=name, field_value=value))
    db.commit()
    return doc


def _check(client, business_no: str, direction: str, role: str, doc_id: int) -> dict:
    res = client.get(
        f"/api/reconciliation/reports/{business_no}/cells/{direction}/{role}/check/{doc_id}"
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_check_flags_receipt_dropped_into_invoice_cell(client, db, ledger):
    """回单拖进发票格 —— 最典型的一种错槽。"""
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    doc = _doc(db, "银行回单.pdf", "回单", amount="234567.89")

    body = _check(client, "YWX101", "up", "invoice", doc.id)
    assert body["ok"] is False
    issue = next(i for i in body["issues"] if i["code"] == "role_mismatch")
    assert "回单" in issue["message"]
    assert issue["suggest_role"] == "payment_receipt"
    assert body["suggest_role"] == "payment_receipt"


def test_check_accepts_matching_invoice(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    doc = _doc(db, "发票.pdf", "发票", amount="234567.89")

    body = _check(client, "YWX101", "up", "invoice", doc.id)
    assert body["ok"] is True
    assert body["issues"] == []
    assert body["pending"] is False


def test_check_flags_direction_by_contract_no(client, db, ledger):
    """下游合同号的单据放进上游格。"""
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    doc = _doc(db, "销售合同.pdf", "合同", contract_no="HSYCXS-101", amount="1020000")

    body = _check(client, "YWX101", "up", "contract", doc.id)
    issue = next(i for i in body["issues"] if i["code"] == "direction_mismatch")
    assert issue["suggest_direction"] == "down"
    assert body["suggest_direction"] == "down"


def test_check_warns_when_party_not_in_ledger(client, db, ledger):
    """公司名既不是供应商也不是采购商 —— 只警告，不拦。"""
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    doc = _doc(db, "发票.pdf", "发票", amount="234567.89", seller="第三家公司")

    body = _check(client, "YWX101", "up", "invoice", doc.id)
    issue = next(i for i in body["issues"] if i["code"] == "party_unknown")
    assert issue["severity"] == "warn"
    assert body["ok"] is True


def test_check_warns_invoice_no_on_another_business(client, db, ledger):
    """同一张发票已经在别的业务线上归档。"""
    _register(db, ledger)
    first = _doc(db, "发票A.pdf", "发票", amount="234567.89", invoice_no="12345678")
    client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": first.id, "direction": "down", "role": "invoice"},
    )
    second = _doc(db, "发票B.pdf", "发票", amount="345678.90", invoice_no="12345678")

    body = _check(client, "YWX102", "up", "invoice", second.id)
    issue = next(i for i in body["issues"] if i["code"] == "invoice_duplicated_elsewhere")
    assert "YWX101" in issue["message"]


def test_check_pending_when_nothing_extracted(client, db, ledger):
    """识别还没跑完的单据：pending，不弹提示。"""
    _register(db, ledger)
    doc = _add_doc(db, "刚上传.pdf", "")  # 无任何字段

    body = _check(client, "YWX101", "up", "invoice", doc.id)
    assert body["pending"] is True
    assert body["issues"] == []


def test_check_unknown_business_and_bad_role(client, db, ledger):
    _register(db, ledger)
    doc = _add_doc(db, "发票.pdf", "发票", amount="234567.89")

    assert client.get(
        f"/api/reconciliation/reports/NOT_EXIST/cells/up/invoice/check/{doc.id}"
    ).status_code == 400
    assert client.get(
        f"/api/reconciliation/reports/YWX101/cells/up/other/check/{doc.id}"
    ).status_code == 400
    assert client.get(
        "/api/reconciliation/reports/YWX101/cells/sideways/invoice/check/99999"
    ).status_code == 400
