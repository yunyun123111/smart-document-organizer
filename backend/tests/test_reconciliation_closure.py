"""差异闭环测试：key 稳定、新增/遗留/已解决、处理类型与撤销。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    src = ExcelSource(name="闭环台账", file_path=str(path), enabled=True, total_sheets=4)
    db.add(src)
    db.commit()
    return src


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return write_ledger(
        tmp_path / "ledger_closure.xlsx",
        contract_rows=[_contract_row("YWX301", "HSYCCG-301", "HSYCXS-301", 1000000, 1020000)],
        settle_rows=[_settle_row("HSYCXS-301", 1000000, 1018000, 18000)],
        pay_rows=[_pay_row("HSYCXS-301", 1000000, 1018000, 18000, 18000)],
    )


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _detail(client) -> dict:
    res = client.get("/api/reconciliation/reports/YWX301")
    assert res.status_code == 200
    return res.json()


def _first_active_diff(detail: dict) -> dict:
    for d in detail["diffs"]:
        if not d.get("ignored"):
            return d
    raise AssertionError("没有可处理的差异")


def test_first_run_has_stable_key_and_no_baseline(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})

    detail = _detail(client)
    diff = _first_active_diff(detail)
    assert diff["key"], "每条差异都要有稳定 key"
    assert diff["is_new"] is None, "首次对账没有可比基准"


def test_second_run_marks_carried_over(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})
    key1 = _first_active_diff(_detail(client))["key"]

    client.post("/api/reconciliation/run", json={"business_no": "YWX301"})
    detail = _detail(client)
    diff = _first_active_diff(detail)
    assert diff["key"] == key1, "同金额同类型的差异，key 必须保持不变"
    assert diff["is_new"] is False, "上轮就有、本轮仍在 → 遗留"


def test_resolved_diff_moves_to_history(client, db, ledger):
    _register(db, ledger)
    # 第一次：发票 900,000，与结算差 118,000
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})
    assert _detail(client)["resolved_diffs"] == []

    # 补齐另一张发票，合计对齐结算 → 差异应消失并进入"已解决"
    _add_doc(db, "发票-301-补.pdf", "增值税发票", "HSYCXS-301", "118000")
    client.post("/api/reconciliation/run", json={"business_no": "YWX301"})
    detail = _detail(client)
    assert detail["resolved_diffs"], "上轮存在的差异本轮消失，必须留下已解决记录"
    assert "AMOUNT_MISMATCH" in {d["type"] for d in detail["resolved_diffs"]}


def test_mark_supplement_keeps_count(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})

    detail = _detail(client)
    diff = _first_active_diff(detail)
    count_before = detail["diff_count"]

    res = client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": diff["key"], "resolution": "supplement", "note": "已联系供应商补开",
    })
    assert res.status_code == 200

    detail2 = _detail(client)
    target = next(d for d in detail2["diffs"] if d["key"] == diff["key"])
    assert target["resolution"]["type"] == "supplement"
    assert target["resolution"]["note"] == "已联系供应商补开"
    assert target["ignored"] is False, "待补单不算关闭"
    assert detail2["diff_count"] == count_before, "处理中不能减少差异数"


def test_mark_false_positive_closes_diff(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})

    detail = _detail(client)
    diff = _first_active_diff(detail)

    res = client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": diff["key"], "resolution": "false_positive", "note": "识别串行，人工已核",
    })
    assert res.status_code == 200

    detail2 = _detail(client)
    target = next(d for d in detail2["diffs"] if d["key"] == diff["key"])
    assert target["ignored"] is True
    assert detail2["diff_count"] == detail["diff_count"] - 1


def test_reopen_restores_diff(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})

    detail = _detail(client)
    diff = _first_active_diff(detail)
    client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": diff["key"], "resolution": "ignore",
    })
    closed = _detail(client)
    assert next(d for d in closed["diffs"] if d["key"] == diff["key"])["ignored"] is True

    res = client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": diff["key"], "resolution": "reopen",
    })
    assert res.status_code == 200
    reopened = _detail(client)
    target = next(d for d in reopened["diffs"] if d["key"] == diff["key"])
    assert target["ignored"] is False
    assert target["resolution"] == {}


def test_resolution_survives_next_run(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})

    diff = _first_active_diff(_detail(client))
    client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": diff["key"], "resolution": "adjust", "note": "财务月底调账",
    })

    # 重跑后差异仍在：处理记录必须继承，而不是被清空
    client.post("/api/reconciliation/run", json={"business_no": "YWX301"})
    target = next(d for d in _detail(client)["diffs"] if d["key"] == diff["key"])
    assert target["resolution"]["type"] == "adjust"
    assert target["is_new"] is False


def test_unknown_resolution_and_missing_key(client, db, ledger):
    _register(db, ledger)
    _add_doc(db, "发票-301.pdf", "增值税发票", "HSYCXS-301", "900000")
    client.post("/api/reconciliation/run", json={})

    res = client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": "nope", "resolution": "supplement",
    })
    assert res.status_code == 404

    res = client.post("/api/reconciliation/reports/YWX301/resolve", json={
        "key": _first_active_diff(_detail(client))["key"], "resolution": "whatever",
    })
    assert res.status_code == 400
