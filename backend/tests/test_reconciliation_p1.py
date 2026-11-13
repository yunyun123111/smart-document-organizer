"""P1 测试：外部明细导入、催办清单、客户/期间视角、发票号跨线校验。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.main import app
from backend.models import Document, DocumentField, ExcelSource, ReconciliationReport, STATUS_PENDING
from backend.services.external_detail_service import (
    normalize_kind,
    parse_amount,
    parse_date,
    parse_workbook,
)
from backend.tests.test_reconciliation_service import (
    _contract_row,
    _pay_row,
    _settle_row,
    write_ledger
)

from sqlalchemy import select


def _add_doc(db, filename: str, doc_type: str, contract_no: str = "",
             amount: str = "", invoice_no: str = "") -> Document:
    doc = Document(
        original_filename=filename,
        original_path=f"/tmp/{filename}",
        current_filename=filename,
        current_path=f"/tmp/{filename}",
        status=STATUS_PENDING,
        document_type=doc_type,
    )
    db.add(doc)
    db.commit()
    for name, value in (("contract_no", contract_no), ("amount", amount), ("invoice_no", invoice_no)):
        if value:
            db.add(DocumentField(document_id=doc.id, field_name=name, field_value=value))
    db.commit()
    return doc


def _register(db, path: Path) -> ExcelSource:
    src = ExcelSource(name="P1 台账", file_path=str(path), enabled=True, total_sheets=4)
    db.add(src)
    db.commit()
    return src


def _write_external(path: Path, rows: list[list], headers: list[str] | None = None) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(headers or ["业务线号", "日期", "金额", "类型", "备注"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return write_ledger(
        tmp_path / "ledger_p1.xlsx",
        contract_rows=[_contract_row("YWX401", "HSYCCG-401", "HSYCXS-401", 1000000, 1020000)],
        settle_rows=[_settle_row("HSYCXS-401", 1000000, 1018000, 18000)],
        pay_rows=[_pay_row("HSYCXS-401", 1000000, 1018000, 18000, 18000)],
    )


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ---------- 解析层 ----------

def test_parse_amount_variants():
    assert str(parse_amount("1,234.56")) == "1234.56"
    assert str(parse_amount("¥ 1,018,000.00")) == "1018000.00"
    assert str(parse_amount("(1,000)")) == "-1000.00"
    assert parse_amount("") is None
    assert parse_amount("摘要") is None


def test_parse_date_variants():
    assert parse_date("2026-05-14").isoformat() == "2026-05-14"
    assert parse_date("2026/5/14").isoformat() == "2026-05-14"
    assert parse_date("2026年5月14日").isoformat() == "2026-05-14"
    assert parse_date("") is None


def test_normalize_kind():
    assert normalize_kind("付款", None) == "payment"
    assert normalize_kind("回款", None) == "receipt"
    assert normalize_kind("", -100) == "payment"
    assert normalize_kind("", 100) == "receipt"


def test_parse_workbook_requires_columns(tmp_path: Path):
    path = _write_external(tmp_path / "bad.xlsx", [[1, 2]], headers=["随便", "写点"])
    with pytest.raises(Exception):
        parse_workbook(path)


# ---------- 导入 API ----------

def test_import_and_list(client, db, tmp_path):
    path = _write_external(tmp_path / "回款明细.xlsx", [
        ["YWX401", "2026-05-14", "3,400,374", "回款", "第一笔"],
        ["YWX401", "2026-05-20", "1,500,000", "回款", ""],
    ])
    with open(path, "rb") as fh:
        res = client.post(
            "/api/reconciliation/external-details/import",
            files={"file": ("回款明细.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"mapping": ""},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["imported"] == 2
    assert body["batch_no"]

    listed = client.get("/api/reconciliation/external-details", params={"business_no": "YWX401"}).json()
    assert listed["total"] == 2
    assert listed["receipt_total"] == pytest.approx(4900374.0)

    batches = client.get("/api/reconciliation/external-details/batches").json()
    assert batches["total"] == 1


def test_import_bad_headers_returns_400(client, tmp_path):
    path = _write_external(tmp_path / "bad.xlsx", [[1, 2]], headers=["随便", "写点"])
    with open(path, "rb") as fh:
        res = client.post(
            "/api/reconciliation/external-details/import",
            files={"file": ("bad.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert res.status_code == 400
    assert "业务线号" in res.json()["detail"]


def test_delete_batch(client, db, tmp_path):
    path = _write_external(tmp_path / "d.xlsx", [["YWX401", "2026-05-14", "1000", "回款", ""]])
    with open(path, "rb") as fh:
        batch = client.post(
            "/api/reconciliation/external-details/import",
            files={"file": ("d.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        ).json()["batch_no"]
    res = client.delete(f"/api/reconciliation/external-details/batches/{batch}")
    assert res.status_code == 200
    assert res.json()["removed"] == 1
    assert client.get("/api/reconciliation/external-details").json()["total"] == 0


def test_external_details_do_not_change_status(client, db, ledger, tmp_path):
    """外部明细只作参考：导进去不能把 no_data 变成 ok、也不能新增差异。"""
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    before = client.get("/api/reconciliation/reports/YWX401").json()

    path = _write_external(tmp_path / "e.xlsx", [["YWX401", "2026-05-14", "1018000", "回款", ""]])
    with open(path, "rb") as fh:
        client.post(
            "/api/reconciliation/external-details/import",
            files={"file": ("e.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    client.post("/api/reconciliation/run", json={"business_no": "YWX401"})
    after = client.get("/api/reconciliation/reports/YWX401").json()

    assert after["status"] == before["status"]
    assert len(after["diffs"]) == len(before["diffs"])

    # 但汇总里能看到外部回款
    summary = client.get("/api/reconciliation/summary", params={"group": "customer"}).json()
    row = summary["rows"][0]
    assert row["external_receipt"] == pytest.approx(1018000.0)


# ---------- 催办 ----------

def test_urge_and_urge_list(client, db, ledger):
    _register(db, ledger)
    # 只挂发票，缺付款回单等 → 出现缺件差异
    _add_doc(db, "发票-401.pdf", "增值税发票", "HSYCXS-401", "1018000")
    client.post("/api/reconciliation/run", json={})

    res = client.post("/api/reconciliation/reports/YWX401/urge", json={"note": "电话催办"})
    assert res.status_code == 200
    assert res.json()["count"] == 1

    client.post("/api/reconciliation/reports/YWX401/urge", json={"note": "再次催办"})
    items = client.get("/api/reconciliation/urge-list").json()["items"]
    target = next(i for i in items if i["business_no"] == "YWX401")
    assert target["urge_count"] == 2
    assert target["last_note"] == "再次催办"
    assert target["missing_roles"]


def test_urge_unknown_business(client):
    res = client.post("/api/reconciliation/reports/NOPE/urge", json={"note": "x"})
    assert res.status_code == 404


# ---------- 聚合 ----------

def test_summary_groups(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-401.pdf", "增值税发票", "HSYCXS-401", "900000")
    client.post("/api/reconciliation/run", json={})

    by_customer = client.get("/api/reconciliation/summary", params={"group": "customer"}).json()
    assert by_customer["total"] >= 1
    row = by_customer["rows"][0]
    assert row["down_settlement"] == pytest.approx(1018000.0)
    assert row["warning_count"] >= 1 or row["diff_count"] >= 1

    by_month = client.get("/api/reconciliation/summary", params={"group": "month"}).json()
    assert by_month["total"] >= 1


def test_summary_rejects_bad_group(client):
    res = client.get("/api/reconciliation/summary", params={"group": "supplier"})
    assert res.status_code == 422 or res.status_code == 400


# ---------- 发票号跨线 ----------

def test_invoice_uniqueness_flags_cross_business(client, db, tmp_path):
    ledger = write_ledger(
        tmp_path / "ledger_two.xlsx",
        contract_rows=[
            _contract_row("YWX501", "HSYCCG-501", "HSYCXS-501", 1000000, 1020000),
            _contract_row("YWX502", "HSYCCG-502", "HSYCXS-502", 1000000, 1020000),
        ],
        settle_rows=[
            _settle_row("HSYCXS-501", 1000000, 1018000, 18000),
            _settle_row("HSYCXS-502", 1000000, 1018000, 18000),
        ],
        pay_rows=[
            _pay_row("HSYCXS-501", 1000000, 1018000, 18000, 18000),
            _pay_row("HSYCXS-502", 1000000, 1018000, 18000, 18000),
        ],
    )
    _register(db, ledger)
    # 同一张发票号挂到两条业务线
    _add_doc(db, "发票-A.pdf", "增值税发票", "HSYCXS-501", "1018000", invoice_no="24512345678901234567")
    _add_doc(db, "发票-B.pdf", "增值税发票", "HSYCXS-502", "1018000", invoice_no="24512345678901234567")
    client.post("/api/reconciliation/run", json={})

    res = client.get("/api/reconciliation/invoice-uniqueness").json()
    assert res["total"] == 1
    assert set(res["items"][0]["business_nos"]) == {"YWX501", "YWX502"}
