"""对账规则通用性回归：全部使用合成业务线与合成单据，不读取任何真实台账或样本资料。

目的：保证规则对「任意业务线 / 任意单据组合」成立，而不是只在某条真实业务线上凑巧通过。
覆盖点：无船名无物料、分批开票、少开/多开、台账未登记利息、业务尚未结算、
凭证缺失、结算单金额识别失败。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.models import (
    DIFF_AMOUNT_MISMATCH,
    DIFF_INTEREST_MISMATCH,
    DIFF_MISSING_DOC,
    DIFF_UNPARSED_AMOUNT,
)
from backend.services.reconciliation_service import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    DocRecord,
    LedgerBusiness,
    ROLE_INVOICE,
    ROLE_PAYMENT_RECEIPT,
    ROLE_SETTLEMENT,
    check_business,
)


def _biz(**overrides) -> LedgerBusiness:
    base = dict(
        business_no="YWX-GEN-0001",
        up_contract_no="CG-GEN-001",
        down_contract_no="XS-GEN-001",
        supplier="上游供应商甲",
        customer="下游客户乙",
    )
    base.update(overrides)
    return LedgerBusiness(**base)


def _doc(did: int, role: str, direction: str, amount=None, no: str = "",
         day: int | None = 10) -> DocRecord:
    return DocRecord(
        document_id=did,
        filename=f"{role}-{did}.pdf",
        contract_no=no,
        amount=Decimal(str(amount)) if amount is not None else None,
        direction=direction,
        role=role,
        doc_date=date(2026, 5, day) if day else None,
    )


def _run(biz: LedgerBusiness, docs: list[DocRecord]):
    return check_business(biz, docs)


def _pick(diffs, type_=None, severity=None, role=None):
    return [d for d in diffs
            if (type_ is None or d.type == type_)
            and (severity is None or d.severity == severity)
            and (role is None or d.role == role)]


# ---------------- 全对齐（无船名、无物料，纯金额口径） ----------------

def test_fully_aligned_business_has_no_diffs():
    """上下游单据齐全且金额一致、利息已登记 → 零差异。"""
    biz = _biz(up_settlement=Decimal("1000000"), down_settlement=Decimal("1010000"),
               interest_received=Decimal("10000"), pay_amount=Decimal("1000000"),
               receipt_amount=Decimal("1010000"))
    docs = [
        _doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1000000, "CG-GEN-001"),
        _doc(2, ROLE_INVOICE, DIRECTION_UP, 1000000, "CG-GEN-001"),
        _doc(3, ROLE_SETTLEMENT, DIRECTION_DOWN, 1010000, "XS-GEN-001"),
        _doc(4, ROLE_INVOICE, DIRECTION_DOWN, 1010000, "XS-GEN-001"),
        _doc(5, ROLE_PAYMENT_RECEIPT, DIRECTION_UP, 1000000, "CG-GEN-001"),
    ]
    diffs, matrix, unlinked = _run(biz, docs)
    assert diffs == [], [f"{d.severity} {d.title}" for d in diffs]
    assert unlinked == []
    assert len(matrix) == 10


# ---------------- 分批开票 ----------------

def test_split_invoices_sum_to_settlement_is_not_duplicate():
    """两张不同日期的发票分批开票，合计等于结算 → 不判重复、不出金额差异。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"),
               down_settlement=Decimal("1010000"), receipt_amount=Decimal("1010000"))
    docs = [
        _doc(1, ROLE_INVOICE, DIRECTION_UP, 600000, "CG-GEN-001", day=10),
        _doc(2, ROLE_INVOICE, DIRECTION_UP, 400000, "CG-GEN-001", day=20),
    ]
    diffs, _, _ = _run(biz, docs)
    assert not _pick(diffs, DIFF_AMOUNT_MISMATCH, role=ROLE_INVOICE)
    assert not [d for d in diffs if "重复" in d.title]


# ---------------- 金额差异按方向分级 ----------------

def test_invoice_below_settlement_is_warning():
    """发票合计低于结算金额（可能尚未开完）→ WARNING，不是错误。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"))
    docs = [_doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1000000, "CG-GEN-001"),
            _doc(2, ROLE_INVOICE, DIRECTION_UP, 900000, "CG-GEN-001")]
    diffs, _, _ = _run(biz, docs)
    items = _pick(diffs, DIFF_AMOUNT_MISMATCH, severity="WARNING", role=ROLE_INVOICE)
    assert len(items) == 1
    assert "尚未开完" in items[0].message


def test_invoice_above_settlement_is_critical():
    """发票合计高于结算金额（多开票）→ CRITICAL。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"))
    docs = [_doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1000000, "CG-GEN-001"),
            _doc(2, ROLE_INVOICE, DIRECTION_UP, 1100000, "CG-GEN-001")]
    diffs, _, _ = _run(biz, docs)
    items = _pick(diffs, DIFF_AMOUNT_MISMATCH, severity="CRITICAL", role=ROLE_INVOICE)
    assert len(items) == 1


def test_settlement_doc_above_ledger_is_critical():
    """结算单金额高于台账（台账漏登记）→ CRITICAL。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"))
    docs = [_doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1200000, "CG-GEN-001"),
            _doc(2, ROLE_INVOICE, DIRECTION_UP, 1000000, "CG-GEN-001")]
    diffs, _, _ = _run(biz, docs)
    assert len(_pick(diffs, DIFF_AMOUNT_MISMATCH, severity="CRITICAL", role=ROLE_SETTLEMENT)) == 1


# ---------------- 利息 ----------------

def test_interest_gap_without_ledger_interest_is_warning():
    """上下游结算有差额但台账两条利息都没登记 → 提示，不允许静默通过。"""
    biz = _biz(up_settlement=Decimal("1000000"), down_settlement=Decimal("1010000"),
               pay_amount=Decimal("1000000"), receipt_amount=Decimal("1010000"))
    docs = [
        _doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1000000, "CG-GEN-001"),
        _doc(2, ROLE_SETTLEMENT, DIRECTION_DOWN, 1010000, "XS-GEN-001"),
    ]
    diffs, _, _ = _run(biz, docs)
    items = _pick(diffs, DIFF_INTEREST_MISMATCH, severity="WARNING")
    assert len(items) == 1
    assert "未登记利息" in items[0].title


def test_interest_gap_matching_ledger_interest_has_no_diff():
    biz = _biz(up_settlement=Decimal("1000000"), down_settlement=Decimal("1010000"),
               interest_receivable=Decimal("10000"))
    docs = [
        _doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1000000, "CG-GEN-001"),
        _doc(2, ROLE_SETTLEMENT, DIRECTION_DOWN, 1010000, "XS-GEN-001"),
    ]
    diffs, _, _ = _run(biz, docs)
    assert not _pick(diffs, DIFF_INTEREST_MISMATCH)


# ---------------- 缺件严重度 ----------------

def test_business_not_settled_missing_docs_are_warnings():
    """台账金额全空（业务尚未结算）时缺件只提示，不判错误。"""
    biz = _biz()
    diffs, _, _ = _run(biz, [])
    missing = _pick(diffs, DIFF_MISSING_DOC)
    assert len(missing) == 5
    assert {d.severity for d in missing} == {"WARNING"}


def test_ledger_has_amount_but_settlement_doc_missing_is_critical():
    """台账已登记结算金额、结算单缺失 → CRITICAL。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"))
    docs = [_doc(1, ROLE_INVOICE, DIRECTION_UP, 1000000, "CG-GEN-001"),
            _doc(2, ROLE_PAYMENT_RECEIPT, DIRECTION_UP, 1000000, "CG-GEN-001")]
    diffs, _, _ = _run(biz, docs)
    items = [d for d in _pick(diffs, DIFF_MISSING_DOC)
             if d.role == ROLE_SETTLEMENT and d.direction == DIRECTION_UP]
    assert len(items) == 1
    assert items[0].severity == "CRITICAL"


def test_missing_payment_voucher_is_warning_not_critical():
    """有付款额但无货款回单：缺件按凭证类提示，另附一条待补说明。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"))
    docs = [_doc(1, ROLE_SETTLEMENT, DIRECTION_UP, 1000000, "CG-GEN-001"),
            _doc(2, ROLE_INVOICE, DIRECTION_UP, 1000000, "CG-GEN-001")]
    diffs, _, _ = _run(biz, docs)
    items = [d for d in _pick(diffs, DIFF_MISSING_DOC) if d.role == ROLE_PAYMENT_RECEIPT]
    assert len(items) == 1
    assert items[0].severity == "WARNING"
    assert [d for d in diffs if "付款凭证未提供" in d.title]


# ---------------- 识别失败 ----------------

def test_settlement_doc_without_amount_is_unparsed_not_missing():
    """结算单归档但金额没抽出来 → 报「金额未识别」，不报「缺件」。"""
    biz = _biz(up_settlement=Decimal("1000000"), pay_amount=Decimal("1000000"))
    docs = [_doc(1, ROLE_SETTLEMENT, DIRECTION_UP, None, "CG-GEN-001"),
            _doc(2, ROLE_INVOICE, DIRECTION_UP, 1000000, "CG-GEN-001"),
            _doc(3, ROLE_PAYMENT_RECEIPT, DIRECTION_UP, 1000000, "CG-GEN-001")]
    diffs, matrix, _ = _run(biz, docs)
    assert len(_pick(diffs, DIFF_UNPARSED_AMOUNT, role=ROLE_SETTLEMENT)) == 1
    assert not [d for d in diffs
                if d.type == DIFF_MISSING_DOC and d.role == ROLE_SETTLEMENT
                and d.direction == DIRECTION_UP]
    up_settle_cell = next(c for c in matrix
                          if c.direction == DIRECTION_UP and c.role == ROLE_SETTLEMENT)
    assert up_settle_cell.count == 1
    assert up_settle_cell.amount == 0
