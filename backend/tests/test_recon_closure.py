"""闭环判定回归：全部用合成业务线与合成矩阵，不读真实台账。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services.recon_closure import (
    STAGE_CLOSED,
    STAGE_COLLECTING,
    STAGE_NOT_STARTED,
    STAGE_PARTIAL,
    STAGE_UNBALANCED,
    compute_closure,
)
from backend.services.reconciliation_service import LedgerBusiness, MatrixCell


def _biz(**overrides) -> LedgerBusiness:
    base = dict(
        business_no="YWX-CLOSE-0001",
        up_contract_no="CG-CLOSE-001",
        down_contract_no="XS-CLOSE-001",
        up_settlement=Decimal("1000000"),
        down_settlement=Decimal("1010000"),
        pay_amount=Decimal("1000000"),
        receipt_amount=Decimal("1010000"),
    )
    base.update(overrides)
    return LedgerBusiness(**base)


def _cells(**spec) -> list[MatrixCell]:
    """spec 形如 up_invoice=2；金额用 up_invoice_amt 指定。"""
    out: list[MatrixCell] = []
    for direction in ("up", "down"):
        for role in ("contract", "settlement", "invoice", "cargo_right", "payment_receipt"):
            count = spec.get(f"{direction}_{role}", 0)
            amount = spec.get(f"{direction}_{role}_amt", 0.0)
            out.append(MatrixCell(direction=direction, role=role,
                                  count=count, amount=float(amount)))
    return out


def _full(**amounts) -> list[MatrixCell]:
    spec = {
        "up_contract": 1, "up_settlement": 1, "up_invoice": 1, "up_payment_receipt": 1,
        "down_contract": 1, "down_settlement": 1, "down_invoice": 1, "down_payment_receipt": 1,
        "up_settlement_amt": 1000000, "up_invoice_amt": 1000000, "up_payment_receipt_amt": 1000000,
        "down_settlement_amt": 1010000, "down_invoice_amt": 1010000,
        "down_payment_receipt_amt": 1010000,
    }
    spec.update(amounts)
    return _cells(**spec)


# ---------------- 已闭环 ----------------

def test_all_sides_complete_and_balanced_is_closed():
    c = compute_closure(_biz(), _full())
    assert c["stage"] == STAGE_CLOSED
    assert c["closed"] is True
    assert c["percent"] == 100
    assert c["blockers"] == []
    assert c["sides"]["up"]["closed"] and c["sides"]["down"]["closed"]


def test_cargo_right_is_optional_and_not_counted():
    """货权单只是可选资料，缺它不影响闭环，也不进完成度分母。"""
    c = compute_closure(_biz(), _full())
    assert c["sides"]["up"]["optional"]["cargo_right"] == 0
    assert c["requirements_total"] == 8
    with_cargo = compute_closure(_biz(), _full(up_cargo_right=2, down_cargo_right=1))
    assert with_cargo["percent"] == 100
    assert with_cargo["requirements_total"] == 8


# ---------------- 缺件 ----------------

def test_missing_down_invoice_is_partial():
    """上游齐且平、下游缺发票 → 单侧闭环，进度 7/8。"""
    c = compute_closure(_biz(), _full(down_invoice=0))
    assert c["stage"] == STAGE_PARTIAL
    assert "下游缺发票" in c["blockers"]
    assert c["percent"] == 88  # 7/8
    assert c["sides"]["up"]["closed"] is True
    assert c["sides"]["down"]["closed"] is False


def test_both_sides_missing_docs_is_collecting():
    c = compute_closure(_biz(), _cells(
        up_contract=1, down_contract=1,
        up_settlement_amt=1000000, down_settlement_amt=1010000,
    ))
    assert c["stage"] == STAGE_COLLECTING
    assert c["percent"] == 25  # 只有两侧合同在
    assert "上游缺结算单" in c["blockers"]


def test_nothing_uploaded_is_not_started():
    c = compute_closure(_biz(), _cells())
    assert c["stage"] == STAGE_NOT_STARTED
    assert c["percent"] == 0
    assert c["sides"]["up"]["requirements"][0]["have"] is False


# ---------------- 金额不平 ----------------

def test_docs_complete_but_invoice_over_is_unbalanced():
    c = compute_closure(_biz(), _full(up_invoice_amt=1200000))
    assert c["stage"] == STAGE_UNBALANCED
    assert c["sides"]["up"]["docs_ok"] is True
    assert c["sides"]["up"]["amount_ok"] is False
    assert any("上游发票合计" in b and "差" in b for b in c["blockers"])


def test_underpaid_upstream_is_not_closed():
    """台账付款 100 万，回单只有 80 万 → 有欠款，不闭环。"""
    c = compute_closure(_biz(), _full(up_payment_receipt_amt=800000))
    assert c["closed"] is False
    assert c["stage"] == STAGE_UNBALANCED
    assert any("上游付款回单合计" in b for b in c["blockers"])


# ---------------- 单侧 / 缺口径 ----------------

def test_one_side_closed_is_partial():
    c = compute_closure(_biz(), _cells(
        up_contract=1, up_settlement=1, up_invoice=1, up_payment_receipt=1,
        up_settlement_amt=1000000, up_invoice_amt=1000000, up_payment_receipt_amt=1000000,
        down_contract=1, down_settlement=1,  # 下游结算在，发票与回款缺
        down_settlement_amt=1010000,
    ))
    assert c["stage"] == STAGE_PARTIAL
    assert "上游已闭环，下游尚未闭环" in c["summary"]


def test_no_down_contract_means_down_not_applicable():
    """下游合同号为空（待双签）时，下游不参与，上游齐了就算闭环。"""
    c = compute_closure(
        _biz(down_contract_no="", down_settlement=None,
             receipt_amount=None),
        _cells(up_contract=1, up_settlement=1, up_invoice=1, up_payment_receipt=1,
               up_settlement_amt=1000000, up_invoice_amt=1000000,
               up_payment_receipt_amt=1000000,
               down_contract=0, down_settlement=0, down_invoice=0,
               down_payment_receipt=0),
    )
    assert c["sides"]["down"]["applicable"] is False
    assert c["stage"] == STAGE_CLOSED
    assert c["requirements_total"] == 4


def test_unregistered_pay_amount_falls_back_to_settlement():
    """台账没登记付款额时，回单按结算额比，闭环成立但要留一条口径提示。"""
    c = compute_closure(_biz(pay_amount=None), _full())
    assert c["closed"] is True
    assert any("台账未登记付款额" in n for n in c["sides"]["up"]["notes"])


def test_missing_amount_on_document_is_not_treated_as_zero():
    """单据金额识别失败（0 份金额）时 amount=None，不能当 0 去比。"""
    c = compute_closure(_biz(), _cells(
        up_contract=1, up_settlement=1, up_invoice=1, up_payment_receipt=1,
        down_contract=1, down_settlement=1, down_invoice=1, down_payment_receipt=1,
        # 金额全部为 0.0（矩阵未给金额），等价于未识别
    ))
    assert c["sides"]["up"]["requirements"][1]["amount_ok"] is None
    assert c["closed"] is False
