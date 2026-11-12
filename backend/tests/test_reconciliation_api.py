"""对账 API 集成测试：五个端点的正常流与错误分支。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import Document, DocumentField, ExcelSource, STATUS_PENDING
from backend.tests.test_reconciliation_service import write_ledger, _contract_row, _settle_row, _pay_row


def _add_doc(db, filename: str, doc_type: str, contract_no: str = "", amount: str = "") -> Document:
    """给某条业务线挂一份已识别单据，让它不再是"未上传资料"。"""
    from pathlib import Path as _P
    doc = Document(
        original_filename=filename,
        original_path=str(_P("/tmp") / filename),
        current_filename=filename,
        current_path=str(_P("/tmp") / filename),
        status=STATUS_PENDING,
        document_type=doc_type,
    )
    db.add(doc)
    db.commit()
    for name, value in (("contract_no", contract_no), ("amount", amount)):
        if value:
            db.add(DocumentField(document_id=doc.id, field_name=name, field_value=value))
    db.commit()
    return doc


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return write_ledger(
        tmp_path / "ledger_api.xlsx",
        contract_rows=[
            _contract_row("YWX101", "HSYCCG-101", "HSYCXS-101", 1000000, 1020000),
            _contract_row("YWX102", "HSYCCG-102", "HSYCXS-102", 2000000, 2050000),
        ],
        settle_rows=[
            _settle_row("HSYCXS-101", 1000000, 1018000, 18000),
            _settle_row("HSYCXS-102", 2000000, 2015000, 15000),
        ],
        pay_rows=[
            _pay_row("HSYCXS-101", 1000000, 1018000, 18000, 18000),
            _pay_row("HSYCXS-102", 2000000, 2015000, 20000, 15000),
        ],
    )


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(db, path: Path) -> ExcelSource:
    src = ExcelSource(name="API 台账", file_path=str(path), enabled=True, total_sheets=4)
    db.add(src)
    db.commit()
    return src


def test_run_returns_400_without_source(client, db):
    res = client.post("/api/reconciliation/run", json={})
    assert res.status_code == 400
    assert "台账" in res.json()["detail"] or "Excel" in res.json()["detail"]


def test_run_success(client, db, ledger):
    _register(db, ledger)
    res = client.post("/api/reconciliation/run", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert body["message"] == "对账完成"


def test_run_single_business(client, db, ledger):
    _register(db, ledger)
    res = client.post("/api/reconciliation/run", json={"business_no": "YWX102"})
    assert res.status_code == 200
    assert res.json()["total"] == 1


def test_list_reports(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    res = client.get("/api/reconciliation/reports")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert {i["business_no"] for i in body["items"]} == {"YWX101", "YWX102"}


def test_list_reports_empty_before_run(client):
    res = client.get("/api/reconciliation/reports")
    assert res.status_code == 200
    assert res.json()["total"] == 0


def test_list_reports_invalid_status(client):
    res = client.get("/api/reconciliation/reports", params={"status": "nope"})
    assert res.status_code == 400


def test_list_reports_keyword(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    res = client.get("/api/reconciliation/reports", params={"keyword": "YWX102"})
    assert res.status_code == 200
    assert res.json()["total"] == 1


def test_list_reports_pagination(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    res = client.get("/api/reconciliation/reports", params={"page": 1, "page_size": 1})
    body = res.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_report_detail_matrix(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    res = client.get("/api/reconciliation/reports/YWX101")
    assert res.status_code == 200
    body = res.json()
    assert len(body["matrix"]) == 10
    # 测试库里这条业务线没有任何单据 -> 记 no_data，不出假差异，并在详情里给出说明
    assert body["status"] == "no_data"
    assert body["diff_count"] == 0
    assert body["diffs"] == []
    assert body["note"]


def test_report_detail_404(client):
    res = client.get("/api/reconciliation/reports/NOT_EXIST")
    assert res.status_code == 404


def test_resolve_ignores_one_diff(client, db, ledger):
    _register(db, ledger)
    # 必须挂一份单据：没有任何资料时记 no_data、不出差异，也就没有可忽略的差异
    _add_doc(db, "采购合同.pdf", "采购合同", contract_no="HSYCCG-101", amount="1000000")
    client.post("/api/reconciliation/run", json={})
    before = client.get("/api/reconciliation/reports/YWX101").json()
    assert before["status"] == "error"
    res = client.post(
        "/api/reconciliation/reports/YWX101/resolve",
        json={"diff_type": "MISSING_DOC", "index": 0},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["diff_count"] == before["diff_count"] - 1

    detail = client.get("/api/reconciliation/reports/YWX101").json()
    missing = [d for d in detail["diffs"] if d["type"] == "MISSING_DOC"]
    assert missing[0]["ignored"] is True


def test_resolve_unknown_type_404(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    res = client.post(
        "/api/reconciliation/reports/YWX101/resolve",
        json={"diff_type": "NO_SUCH_TYPE", "index": 0},
    )
    assert res.status_code == 404


def test_resolve_index_out_of_range_400(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "采购合同.pdf", "采购合同", contract_no="HSYCCG-101", amount="1000000")
    client.post("/api/reconciliation/run", json={})
    res = client.post(
        "/api/reconciliation/reports/YWX101/resolve",
        json={"diff_type": "MISSING_DOC", "index": 99},
    )
    assert res.status_code == 400


def test_unlinked_endpoint(client, db, ledger):
    _register(db, ledger)
    res = client.get("/api/reconciliation/unlinked")
    assert res.status_code == 200
    assert res.json()["total"] == 0


def test_unlinked_returns_400_without_source(client, db):
    res = client.get("/api/reconciliation/unlinked")
    assert res.status_code == 400
