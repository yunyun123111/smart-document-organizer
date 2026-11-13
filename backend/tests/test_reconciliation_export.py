"""对账结果导出测试：四个 sheet、筛选、空库不报错。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.main import app
from backend.models import Document, DocumentField, ExcelSource, STATUS_PENDING
from backend.tests.test_reconciliation_service import (
    _contract_row,
    _pay_row,
    _settle_row,
    write_ledger,
)


def _add_doc(db, filename: str, doc_type: str, contract_no: str = "", amount: str = "") -> Document:
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
    for name, value in (("contract_no", contract_no), ("amount", amount)):
        if value:
            db.add(DocumentField(document_id=doc.id, field_name=name, field_value=value))
    db.commit()
    return doc


def _register(db, path: Path) -> ExcelSource:
    src = ExcelSource(name="导出测试台账", file_path=str(path), enabled=True, total_sheets=4)
    db.add(src)
    db.commit()
    return src


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return write_ledger(
        tmp_path / "ledger_export.xlsx",
        contract_rows=[
            _contract_row("YWX201", "HSYCCG-201", "HSYCXS-201", 1000000, 1020000),
            _contract_row("YWX202", "HSYCCG-202", "HSYCXS-202", 2000000, 2050000),
        ],
        settle_rows=[
            _settle_row("HSYCXS-201", 1000000, 1018000, 18000),
            _settle_row("HSYCXS-202", 2000000, 2015000, 15000),
        ],
        pay_rows=[
            _pay_row("HSYCXS-201", 1000000, 1018000, 18000, 18000),
            _pay_row("HSYCXS-202", 2000000, 2015000, 20000, 15000),
        ],
    )


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_export_returns_xlsx_with_four_sheets(client, db, ledger):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})

    res = client.get("/api/reconciliation/export")
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"]
    assert res.content[:2] == b"PK"  # xlsx 是 zip

    wb = load_workbook(BytesIO(res.content))
    assert wb.sheetnames == ["对账明细", "差异明细", "待补资料", "未归属单据"]

    ws = wb["对账明细"]
    assert ws.max_row == 3  # 表头 + 2 条业务线
    assert ws.cell(row=2, column=1).value in {"YWX201", "YWX202"}
    # 金额列必须是数字（可被 Excel 求和），不能是字符串
    assert isinstance(ws.cell(row=2, column=9).value, (int, float))


def test_export_lists_amount_diff_with_labels(client, db, ledger):
    _register(db, ledger)
    # 上游发票 900,000 < 结算 1,018,000：应出现"金额不符/提示"差异
    _add_doc(db, "发票-201.pdf", "增值税发票", "HSYCXS-201", "900000")
    client.post("/api/reconciliation/run", json={})

    res = client.get("/api/reconciliation/export")
    wb = load_workbook(BytesIO(res.content))
    ws = wb["差异明细"]
    assert ws.max_row >= 2
    types = {ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)}
    assert "金额不符" in types
    rows = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert "YWX201" in rows


def test_export_missing_doc_sheet(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-201.pdf", "增值税发票", "HSYCXS-201", "1018000")
    client.post("/api/reconciliation/run", json={})

    wb = load_workbook(BytesIO(client.get("/api/reconciliation/export").content))
    ws = wb["待补资料"]
    assert ws.max_row >= 2
    roles = {ws.cell(row=r, column=3).value for r in range(2, ws.max_row + 1)}
    assert roles  # 至少列出缺失环节


def test_export_status_filter(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-201.pdf", "增值税发票", "HSYCXS-201", "900000")
    client.post("/api/reconciliation/run", json={})

    wb_all = load_workbook(BytesIO(client.get("/api/reconciliation/export").content))
    wb_ok = load_workbook(
        BytesIO(client.get("/api/reconciliation/export", params={"status": "no_data"}).content)
    )
    assert wb_all["对账明细"].max_row >= 2
    # 过滤后只保留 no_data（YWX202 没有挂单据）
    for r in range(2, wb_ok["对账明细"].max_row + 1):
        assert wb_ok["对账明细"].cell(row=r, column=16).value == "未上传资料"


def test_export_empty_db_is_valid(client):
    res = client.get("/api/reconciliation/export")
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    assert wb["对账明细"].max_row == 1  # 只有表头


def test_export_rejects_unknown_status(client):
    res = client.get("/api/reconciliation/export", params={"status": "bogus"})
    assert res.status_code == 400
