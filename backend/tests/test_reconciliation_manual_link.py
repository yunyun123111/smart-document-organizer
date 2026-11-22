"""P2：未归属候选清单 + 槽位挂载（缺口驱动补单的后端闭环）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.tests.test_reconciliation_api import _add_doc, _register, ledger  # noqa: F401


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _cell(detail: dict, direction: str, role: str) -> dict:
    return next(c for c in detail["matrix"] if c["direction"] == direction and c["role"] == role)


def _link(client, doc_id: int, recompute: bool = True, **cell) -> None:
    body = {"document_id": doc_id, **cell}
    client.post(
        "/api/reconciliation/reports/YWX101/link",
        params={"recompute": recompute},
        json=body,
    )


def test_candidates_returns_pool_invoice(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "未归属发票.pdf", "发票", amount="123456.78")

    res = client.get("/api/reconciliation/reports/YWX101/cells/up/invoice/candidates")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    doc = body["documents"][0]
    assert doc["reason"].startswith("没有合同号")
    assert doc["amount"] == 123456.78


def test_candidates_filtered_by_role(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "未归属发票.pdf", "发票", amount="123456.78")

    res = client.get("/api/reconciliation/reports/YWX101/cells/up/contract/candidates")
    assert res.json()["total"] == 0


def test_candidates_unknown_business_400(client, db, ledger):
    _register(db, ledger)
    res = client.get("/api/reconciliation/reports/NOT_EXIST/cells/up/invoice/candidates")
    assert res.status_code == 400


def test_link_moves_candidate_into_cell(client, db, ledger):
    _register(db, ledger)
    doc = _add_doc(db, "未归属发票.pdf", "发票", amount="123456.78")
    client.post("/api/reconciliation/run", json={})
    assert _cell(client.get("/api/reconciliation/reports/YWX101").json(), "up", "invoice")["count"] == 0

    res = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice", "note": "补单"},
    )
    assert res.status_code == 200
    assert res.json()["message"] == "已挂载并重新对账"

    detail = client.get("/api/reconciliation/reports/YWX101").json()
    cell = _cell(detail, "up", "invoice")
    assert cell["count"] == 1
    assert cell["amount"] == 123456.78

    # 已人工归属 -> 不再出现在候选里
    assert client.get("/api/reconciliation/reports/YWX101/cells/up/invoice/candidates").json()["total"] == 0
    # 明细抽屉能拿到这张单据
    docs = client.get("/api/reconciliation/reports/YWX101/cells/up/invoice/documents").json()
    assert docs["total"] == 1
    assert docs["documents"][0]["document_id"] == doc.id


def test_link_without_recompute_keeps_report_then_batch_recompute(client, db, ledger):
    """批量挂载：前面几份只落归属，最后一份触发一次重算。"""
    _register(db, ledger)
    a = _add_doc(db, "发票A.pdf", "发票", amount="123456.78")
    b = _add_doc(db, "发票B.pdf", "发票", amount="234567.89")
    client.post("/api/reconciliation/run", json={})

    _link(client, a.id, recompute=False, direction="up", role="invoice")
    assert _cell(client.get("/api/reconciliation/reports/YWX101").json(), "up", "invoice")["count"] == 0

    _link(client, b.id, recompute=True, direction="up", role="invoice")
    cell = _cell(client.get("/api/reconciliation/reports/YWX101").json(), "up", "invoice")
    assert cell["count"] == 2
    assert cell["amount"] == pytest.approx(358024.67)


def test_unlink_restores_candidate(client, db, ledger):
    _register(db, ledger)
    doc = _add_doc(db, "未归属发票.pdf", "发票", amount="123456.78")
    client.post("/api/reconciliation/run", json={})
    _link(client, doc.id, direction="up", role="invoice")

    res = client.delete(f"/api/reconciliation/reports/YWX101/link/{doc.id}")
    assert res.status_code == 200
    assert res.json()["removed"] is True

    assert _cell(client.get("/api/reconciliation/reports/YWX101").json(), "up", "invoice")["count"] == 0
    assert client.get("/api/reconciliation/reports/YWX101/cells/up/invoice/candidates").json()["total"] == 1


def test_unlink_missing_is_idempotent(client, db, ledger):
    _register(db, ledger)
    res = client.delete("/api/reconciliation/reports/YWX101/link/999")
    assert res.status_code == 200
    assert res.json()["removed"] is False


def test_link_bad_role_400(client, db, ledger):
    _register(db, ledger)
    doc = _add_doc(db, "未归属发票.pdf", "发票", amount="123456.78")
    res = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "not_a_role"},
    )
    assert res.status_code == 400


def test_link_unknown_document_400(client, db, ledger):
    _register(db, ledger)
    res = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": 12345, "direction": "up", "role": "invoice"},
    )
    assert res.status_code == 400

