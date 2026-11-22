"""P2-3：对账 ↔ 业务档案双向打通。

链路一（对账 → 档案）：补单挂到业务线槽位时，按方向挑合同号写进档案；
链路二（档案 → 对账）：档案新增文件后，反查合同所属业务线并重算矩阵。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import app
from backend.models import BusinessFile, BusinessRecord
from backend.tests.test_reconciliation_api import _add_doc, _register, ledger  # noqa: F401


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _record(db, contract_no: str) -> BusinessRecord | None:
    return db.execute(
        select(BusinessRecord).where(BusinessRecord.business_no == contract_no)
    ).scalar_one_or_none()


def _files_of(db, record: BusinessRecord) -> list[BusinessFile]:
    return db.execute(
        select(BusinessFile).where(BusinessFile.business_id == record.id)
    ).scalars().all()


def test_link_creates_archive_with_up_contract(client, db, ledger):
    """上游发票补单 → 落进采购合同档案，角色是 invoice。"""
    _register(db, ledger)
    doc = _add_doc(db, "上游发票.pdf", "发票", amount="123456.78")

    res = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice", "note": "补单"},
    )
    assert res.status_code == 200
    archive = res.json()["archive"]
    assert archive["action"] == "created"
    assert archive["contract_no"] == "HSYCCG-101"

    rec = _record(db, "HSYCCG-101")
    assert rec is not None
    assert rec.business_type == "采购"
    files = _files_of(db, rec)
    assert len(files) == 1
    assert files[0].document_id == doc.id
    assert files[0].file_role == "invoice"
    assert files[0].link_source == "recon_link"


def test_unlink_revokes_archive_link_so_candidate_returns(client, db, ledger):
    """取消对账关联要连档案里的补单关联一起撤，否则单据回不到未归属候选。"""
    _register(db, ledger)
    doc = _add_doc(db, "未归属发票.pdf", "发票", amount="123456.78")

    client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice"},
    )
    assert client.get(
        "/api/reconciliation/reports/YWX101/cells/up/invoice/candidates"
    ).json()["total"] == 0

    res = client.delete(f"/api/reconciliation/reports/YWX101/link/{doc.id}")
    assert res.status_code == 200
    assert res.json()["archive_removed"] == 1

    # 档案关联撤掉后，单据重新回到「库中待归位」
    assert client.get(
        "/api/reconciliation/reports/YWX101/cells/up/invoice/candidates"
    ).json()["total"] == 1


def test_link_down_uses_sales_contract(client, db, ledger):
    _register(db, ledger)
    doc = _add_doc(db, "下游回单.pdf", "回单", amount="234567.89")

    res = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "down", "role": "payment_receipt"},
    )
    archive = res.json()["archive"]
    assert archive["contract_no"] == "HSYCXS-101"
    rec = _record(db, "HSYCXS-101")
    # 回单在档案里没有专属角色，落 other，但对账侧仍按类型认回单
    assert _files_of(db, rec)[0].file_role == "other"


def test_relink_is_idempotent(client, db, ledger):
    _register(db, ledger)
    doc = _add_doc(db, "发票.pdf", "发票", amount="123456.78")

    first = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice"},
    ).json()["archive"]
    second = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice"},
    ).json()["archive"]

    assert first["action"] == "created"
    assert second["action"] == "exists"
    assert len(_files_of(db, _record(db, "HSYCCG-101"))) == 1


def test_archive_add_file_triggers_recompute(client, db, ledger):
    """档案侧归集后，对账自动重算并把这批文件带进矩阵。"""
    _register(db, ledger)
    client.post("/api/reconciliation/run", json={})
    doc = _add_doc(db, "档案补的发票.pdf", "发票", amount="123456.78")

    # 先建一个档案（模拟用户先建合同档案，再把发票挂进来）
    created = client.post(
        "/api/business-archives",
        json={"business_no": "HSYCCG-101", "title": "采购合同 101"},
    )
    assert created.status_code == 201
    business_id = created.json()["data"]["id"]

    res = client.post(
        f"/api/business-archives/{business_id}/files",
        json={"document_id": doc.id, "file_role": "invoice"},
    )
    assert res.status_code == 201
    assert res.json()["recomputed"] == ["YWX101"]

    # 对账侧：这份单据应当已经出现在上游发票格
    detail = client.get("/api/reconciliation/reports/YWX101").json()
    cell = next(c for c in detail["matrix"] if c["direction"] == "up" and c["role"] == "invoice")
    assert doc.id in cell["document_ids"]


def test_archive_backfill_covers_existing_matrix(client, db, ledger):
    """存量回填：矩阵里已归属但没进档案的单据，一次补齐。"""
    _register(db, ledger)
    doc = _add_doc(db, "合同扫描.pdf", "合同", contract_no="HSYCCG-101")
    client.post("/api/reconciliation/run", json={})

    # 自动归集已写过档案（归档链路），先删掉模拟"只有对账、没有档案"的历史状态
    for row in db.execute(select(BusinessFile)).scalars().all():
        db.delete(row)
    db.commit()

    stats = client.post("/api/reconciliation/archive-backfill").json()
    assert stats["scanned"] >= 1
    assert stats["created"] >= 1

    rec = _record(db, "HSYCCG-101")
    assert rec is not None
    assert any(f.document_id == doc.id for f in _files_of(db, rec))


@pytest.fixture()
def ledger_alias(tmp_path):
    """同一份采购合同在台账里同时有主号与 OA 别名（两套编号体系）。"""
    from backend.tests.test_reconciliation_service import (
        _contract_row, _pay_row, _settle_row, write_ledger,
    )

    return write_ledger(
        tmp_path / "ledger_alias.xlsx",
        contract_rows=[
            _contract_row("YWX201", "HSYCCG-201", "HSYCXS-201", 1000000, 1020000, up_alt="BOTC-201"),
        ],
        settle_rows=[_settle_row("HSYCXS-201", 1000000, 1018000, 18000)],
        pay_rows=[_pay_row("HSYCXS-201", 1000000, 1018000, 18000, 18000)],
    )


def test_link_reuses_archive_by_alias(client, db, ledger_alias):
    """档案已按别名（OA 号）建过：补单要复用它，不能给同一合同再建一个档案。"""
    _register(db, ledger_alias)
    alt = BusinessRecord(business_no="BOTC-201", title="OA 号档案")
    db.add(alt)
    db.commit()

    doc = _add_doc(db, "发票.pdf", "发票", amount="123456.78")
    res = client.post(
        "/api/reconciliation/reports/YWX201/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice"},
    )
    archive = res.json()["archive"]
    assert archive["contract_no"] == "BOTC-201"
    assert archive["created_record"] is False

    records = db.execute(select(BusinessRecord)).scalars().all()
    assert len(records) == 1


def test_link_reports_conflict_when_already_manual_elsewhere(client, db, ledger):
    """单据已人工归到别的档案：不搬走，返回 conflict，但挂载本身仍成功。"""
    _register(db, ledger)
    doc = _add_doc(db, "发票.pdf", "发票", amount="123456.78")
    other = BusinessRecord(business_no="HSYCXS-999", title="别的档案")
    db.add(other)
    db.commit()
    db.add(BusinessFile(
        business_id=other.id, document_id=doc.id, file_role="invoice", link_source="manual",
    ))
    db.commit()

    res = client.post(
        "/api/reconciliation/reports/YWX101/link",
        json={"document_id": doc.id, "direction": "up", "role": "invoice"},
    )
    assert res.status_code == 200
    archive = res.json()["archive"]
    assert archive["action"] == "conflict"
    assert "人工归集" in archive["reason"]
    assert db.execute(
        select(BusinessFile).where(BusinessFile.business_id == other.id)
    ).scalars().all()
