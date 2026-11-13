"""P2 测试：定时巡检配置、cron 校验、变化检测、提醒清空。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import Document, DocumentField, ExcelSource, STATUS_PENDING
from backend.services import reconciliation_scheduler as sched
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
    src = ExcelSource(name="P2 台账", file_path=str(path), enabled=True, total_sheets=4)
    db.add(src)
    db.commit()
    return src


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return write_ledger(
        tmp_path / "ledger_p2.xlsx",
        contract_rows=[_contract_row("YWX601", "HSYCCG-601", "HSYCXS-601", 1000000, 1020000)],
        settle_rows=[_settle_row("HSYCXS-601", 1000000, 1018000, 18000)],
        pay_rows=[_pay_row("HSYCXS-601", 1000000, 1018000, 18000, 18000)],
    )


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def state_in_tmp(tmp_path: Path, monkeypatch):
    """把调度状态文件放到 tmp，避免污染真实 data/ 目录。"""
    monkeypatch.setattr(sched, "_state_file", lambda: tmp_path / "schedule.json")
    monkeypatch.setattr(sched, "_state", {
        "enabled": False, "cron": "0 8 * * *", "last_run_at": "",
        "last_result": "", "changed_businesses": [], "last_error": "",
    })
    yield
    sched.stop()


# ---------- cron 解析 ----------

def test_parse_cron_accepts_daily():
    assert sched._parse_cron("0 8 * * *") == (0, 8)
    assert sched._parse_cron("30 23 * * *") == (30, 23)


def test_parse_cron_rejects_unsupported():
    for bad in ("*/5 * * * *", "0 8 * * 1", "0 8 * *", "abc 8 * * *", "0 25 * * *"):
        with pytest.raises(ValueError):
            sched._parse_cron(bad)


# ---------- API ----------

def test_schedule_api_roundtrip(client, state_in_tmp):
    initial = client.get("/api/reconciliation/schedule").json()
    assert initial["enabled"] is False

    res = client.put("/api/reconciliation/schedule", json={"enabled": True, "cron": "30 8 * * *"})
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["cron"] == "30 8 * * *"

    client.put("/api/reconciliation/schedule", json={"enabled": False})
    assert client.get("/api/reconciliation/schedule").json()["enabled"] is False


def test_schedule_api_rejects_bad_cron(client, state_in_tmp):
    res = client.put("/api/reconciliation/schedule", json={"cron": "*/10 * * * *"})
    assert res.status_code == 400
    assert "每天固定时刻" in res.json()["detail"]


def test_clear_changes(client, state_in_tmp):
    client.put("/api/reconciliation/schedule", json={"enabled": True})
    sched._state["changed_businesses"] = ["YWX601"]
    res = client.post("/api/reconciliation/schedule/clear-changes")
    assert res.status_code == 200
    assert res.json()["changed_businesses"] == []


# ---------- 变化检测 ----------

def test_run_once_reports_no_change_when_stable(client, db, ledger, state_in_tmp):
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})

    result = sched.run_once(reason="test")
    assert result["ok"] is True
    assert result["changed"] == [], "对账结果没变，不应报变化"


def test_run_once_reports_change_when_diff_grows(client, db, ledger, state_in_tmp):
    _register(db, ledger)
    _add_doc(db, "发票-601.pdf", "增值税发票", "HSYCXS-601", "1018000")
    client.post("/api/reconciliation/run", json={})

    # 补一张金额对不上的发票 → 差异数增加
    _add_doc(db, "发票-601-错.pdf", "增值税发票", "HSYCXS-601", "50000")
    result = sched.run_once(reason="test")
    assert result["ok"] is True
    assert "YWX601" in result["changed"]


def test_run_once_survives_without_ledger(db, state_in_tmp):
    """没有配置台账时不能把线程打挂，错误要落到 last_error。"""
    result = sched.run_once(reason="test")
    assert result["ok"] is False
    assert sched.get_schedule()["last_error"]
