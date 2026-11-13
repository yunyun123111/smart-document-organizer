"""业务线台账自动对账：台账读取、合并规则、R1–R7 规则引擎测试。"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.models import (
    DIFF_AMOUNT_MISMATCH,
    DIFF_INTEREST_MISMATCH,
    DIFF_MISSING_DOC,
    Document,
    ExcelSource,
    ReconciliationReport,
)
from backend.services.reconciliation_service import (
    DIRECTION_DOWN,
    DIRECTION_UNKNOWN,
    DIRECTION_UP,
    DocRecord,
    LedgerBusiness,
    LedgerReader,
    ReconciliationError,
    ReconciliationService,
    ROLE_INVOICE,
    ROLE_CARGO_RIGHT,
    ROLE_SETTLEMENT,
    ROLE_PAYMENT_RECEIPT,
    NATURE_REFUND,
    NATURE_PAYMENT,
    check_business,
    assign_documents,
    contract_variants,
    infer_role,
    norm_no,
    to_date,
    to_decimal,
)
from backend.services.reconciliation_service import _statement_mentions, _load_documents


CONTRACT_HEADERS = [
    "采购合同签订日期", "供应商", "港口", "船名", "物料名称", "采购单价", "数量",
    "采购合同金额", "销售合同编号OA", "2.0编号", "采购合同编号", "2.0编号",
    "业务线", "销售合同签订日期", "采购商名称", "销售单价", "销售合同金额",
]
SETTLE_HEADERS = [
    "抬头", "供应商", "船名", "采购单价", "数量", "销售合同编号OA", "采购商名称",
    "销售合同单价", "结算数量", "采购结算金额", "尾数", "尾票", "合计",
    "实收利息", "销售结算数", "销售结算金额", "收款", "备注", "退款",
]
PAY_HEADERS = [
    "签约单位", "销售合同编号", "销售客户", "保证金到账日期", "保证金金额", "供应商",
    "物料", "采购合同编号", "支付日期", "付款方式", "支付金额", "到期日期", "回款日期",
    "回款方式", "回款金额", "到期剩余天数", "占资天数", "应收利息", "实收利息",
    "利息到账日", "利息结算", "销售开票日期",
]


def _contract_row(biz: str, up_no: str, down_no: str, up_amt, down_amt,
                  up_alt: str = "", down_alt: str = "") -> list:
    row = [None] * len(CONTRACT_HEADERS)
    row[7] = up_amt
    row[8] = down_no
    row[9] = down_alt or None
    row[10] = up_no
    row[11] = up_alt or None
    row[12] = biz
    row[16] = down_amt
    row[1] = "上游供应商"
    row[14] = "下游客户"
    row[3] = "MV TEST"
    return row


def _settle_row(down_no: str, up_settle, down_settle, interest) -> list:
    row = [None] * len(SETTLE_HEADERS)
    row[5] = down_no
    row[9] = up_settle
    row[12] = up_settle
    row[13] = interest
    row[15] = down_settle
    return row


def _pay_row(down_no: str, pay: float, receipt: float, r_interest: float, s_interest: float) -> list:
    row = [None] * len(PAY_HEADERS)
    row[1] = down_no
    row[10] = pay
    row[14] = receipt
    row[17] = r_interest
    row[18] = s_interest
    return row


def write_ledger(path: Path, contract_rows: list[list], settle_rows: list[list] | None = None,
                 pay_rows: list[list] | None = None, aux_rows: list[list] | None = None) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "云创总合同"
    ws.append(CONTRACT_HEADERS)
    for row in contract_rows:
        ws.append(row)

    ws2 = wb.create_sheet("结算数据")
    ws2.append(SETTLE_HEADERS)
    for row in settle_rows or []:
        ws2.append(row)

    ws3 = wb.create_sheet("宏达收付款台账新")
    ws3.append(PAY_HEADERS)
    for row in pay_rows or []:
        ws3.append(row)

    if aux_rows is not None:
        ws4 = wb.create_sheet("物流合同")
        ws4.append(CONTRACT_HEADERS)
        for row in aux_rows:
            ws4.append(row)

    wb.save(str(path))
    return path


@pytest.fixture()
def ledger_path(tmp_path: Path) -> Path:
    """三条业务线：全对齐 / 利息按实收命中 / 利息完全不符。"""
    return write_ledger(
        tmp_path / "ledger.xlsx",
        contract_rows=[
            _contract_row("YWX001", "HSYCCG-0001", "HSYCXS-0001", 1000000, 1020000),
            _contract_row("YWX002", "HSYCCG-0002", "HSYCXS-0002", 2000000, 2050000),
            _contract_row("YWX003", "HSYCCG-0003", "HSYCXS-0003", 3000000, 3100000),
        ],
        settle_rows=[
            _settle_row("HSYCXS-0001", 1000000, 1018000, 18000),
            _settle_row("HSYCXS-0002", 2000000, 2015000, 15000),
            _settle_row("HSYCXS-0003", 3000000, 3016000, 16000),
        ],
        pay_rows=[
            _pay_row("HSYCXS-0001", 1000000, 1018000, 18000, 18000),
            _pay_row("HSYCXS-0002", 2000000, 2015000, 20000, 15000),
            _pay_row("HSYCXS-0003", 3050000, 3016000, 20000, 20000),
        ],
    )


@pytest.fixture()
def loaded(ledger_path: Path) -> list[LedgerBusiness]:
    reader = LedgerReader(str(ledger_path))
    try:
        return reader.load()
    finally:
        reader.close()


# ---------------- 台账读取 ----------------

def test_reads_three_business_lines(loaded):
    assert len(loaded) == 3
    assert {b.business_no for b in loaded} == {"YWX001", "YWX002", "YWX003"}
    assert loaded[0].ledger_sheet == "云创总合同"


# ---------------- 货权单判向（放货给云创/陕建 = 上游，其余 = 下游） ----------------

def test_cargo_direction_by_receiver(loaded):
    biz = loaded[0]   # 船名 MV TEST，供应商「上游供应商」，客户「下游客户」
    up_doc = DocRecord(
        document_id=1, role=ROLE_CARGO_RIGHT,
        text="放货通知 请将我司MV TEST船项下5000湿吨货权"
             "转移给“华山云创供应链科技(山西)有限公司”。",
    )
    down_doc = DocRecord(
        document_id=2, role=ROLE_CARGO_RIGHT,
        text="提货通知单 公司名称：下游客户有限公司 货物名称：PB粉 船名：MV TEST",
    )
    assign_documents([up_doc, down_doc], biz)
    assert up_doc.direction == DIRECTION_UP        # 收货方是我方主体 → 上游
    assert down_doc.direction == DIRECTION_DOWN    # 提货权转给终端客户 → 下游


def test_cargo_unknown_keeps_unassigned(loaded):
    biz = loaded[0]
    # 船名对不上：不是本业务线的单据，不硬判
    other_vessel = DocRecord(
        document_id=3, role=ROLE_CARGO_RIGHT,
        text="放货通知 请将OTHER船项下货权转移给“华山云创供应链科技(山西)有限公司”。",
    )
    # 船名对上、但正文既无接收方也无我方主体：仍判不出方向
    no_receiver = DocRecord(
        document_id=4, role=ROLE_CARGO_RIGHT,
        text="放货通知 MV TEST 船 5000 湿吨 PB 粉。",
    )
    assign_documents([other_vessel, no_receiver], biz)
    assert other_vessel.direction == DIRECTION_UNKNOWN
    assert no_receiver.direction == DIRECTION_UNKNOWN


def test_contract_amounts_and_numbers(loaded):
    biz = loaded[0]
    assert biz.up_contract_no == "HSYCCG-0001"
    assert biz.down_contract_no == "HSYCXS-0001"
    assert biz.up_contract_amount == Decimal("1000000")
    assert biz.down_contract_amount == Decimal("1020000")


def test_settlement_bound_by_down_contract(loaded):
    biz = loaded[2]
    assert biz.up_settlement == Decimal("3000000")
    assert biz.down_settlement == Decimal("3016000")
    assert biz.settlement_missing is False


def test_payment_bound_by_down_contract(loaded):
    biz = loaded[1]
    assert biz.pay_amount == Decimal("2000000")
    assert biz.receipt_amount == Decimal("2015000")
    assert biz.interest_receivable == Decimal("20000")
    assert biz.interest_received == Decimal("15000")
    assert biz.payment_missing is False


def test_missing_main_sheet_raises(tmp_path):
    wb = Workbook()
    wb.active.title = "随便一个表"
    path = tmp_path / "bad.xlsx"
    wb.save(str(path))
    reader = LedgerReader(str(path))
    try:
        with pytest.raises(ReconciliationError):
            reader.load()
    finally:
        reader.close()


def test_absent_file_raises(tmp_path):
    with pytest.raises(ReconciliationError):
        LedgerReader(str(tmp_path / "not_exists.xlsx"))


def test_same_business_multiple_rows_accumulate(tmp_path):
    """同表同业务线多行（多船期）金额累加。"""
    path = write_ledger(
        tmp_path / "multi.xlsx",
        contract_rows=[
            _contract_row("YWX010", "HSYCCG-010", "HSYCXS-010", 100, 120),
            _contract_row("YWX010", "HSYCCG-011", "HSYCXS-011", 200, 240),
        ],
    )
    reader = LedgerReader(str(path))
    try:
        bizs = reader.load()
    finally:
        reader.close()
    assert len(bizs) == 1
    assert bizs[0].up_contract_amount == Decimal("300")
    assert bizs[0].down_contract_amount == Decimal("360")
    assert len(bizs[0].up_contract_nos) == 2


def test_aux_sheet_only_contributes_contract_numbers(tmp_path):
    """物流合同表只补 SJWL 编号，不新增业务线、不累加金额。"""
    import zipfile
    path = write_ledger(
        tmp_path / "logistics.xlsx",
        contract_rows=[_contract_row("YWX020", "HSYCCG-020", "HSYCXS-020", 100, 120)],
        aux_rows=[_contract_row("YWX020", "SJWLCG-020", "SJWLXS-020", 100, 120)],
    )
    assert path.stat().st_size > 1000, f"size={path.stat().st_size}"
    assert zipfile.is_zipfile(path), "生成的文件不是合法 zip"
    reader = LedgerReader(str(path))
    try:
        bizs = reader.load()
    finally:
        reader.close()
    assert len(bizs) == 1
    biz = bizs[0]
    assert biz.up_contract_amount == Decimal("100")
    assert biz.down_contract_amount == Decimal("120")
    assert "HSYCCG-020" in biz.up_contract_nos
    assert "SJWLCG-020" in biz.up_contract_nos
    assert "SJWLXS-020" in biz.down_contract_nos


def test_aux_business_not_in_main_is_ignored(tmp_path):
    path = write_ledger(
        tmp_path / "aux2.xlsx",
        contract_rows=[_contract_row("YWX021", "HSYCCG-021", "HSYCXS-021", 100, 120)],
        aux_rows=[_contract_row("YWX999", "SJWLCG-999", "SJWLXS-999", 100, 120)],
    )
    reader = LedgerReader(str(path))
    try:
        bizs = reader.load()
    finally:
        reader.close()
    assert [b.business_no for b in bizs] == ["YWX021"]


# ---------------- 工具函数 ----------------

def test_contract_variants_strips_brackets():
    variants = contract_variants("HSYCXS（DD）-2026-0027")
    assert "HSYCXS(DD)-2026-0027" in variants
    assert "HSYCXS-2026-0027" in variants


def test_norm_no_fullwidth_brackets_and_space():
    assert norm_no(" hsycxs（dd）-01 ") == "HSYCXS(DD)-01"
    assert norm_no(None) == ""


def test_to_decimal_chinese_amount():
    assert to_decimal("人民币 1,234.50 元") is None  # 带币种前缀无法直接解析
    assert to_decimal("￥1,234.50") == Decimal("1234.50")
    assert to_decimal("") is None


def test_to_decimal_negative_accounting():
    assert to_decimal("(120.5)") == Decimal("-120.5")


def test_to_date_excel_serial():
    assert to_date(45000) is not None
    assert to_date("2026-08-20") is not None
    assert to_date("") is None


# ---------------- 规则引擎 ----------------

def _diffs_for(biz: LedgerBusiness, docs: list[DocRecord] | None = None):
    diffs, matrix, unlinked = check_business(biz, docs or [])
    return diffs, matrix, unlinked


def test_empty_library_reports_five_missing_cells(loaded):
    """核对范围：上游 3 类 + 下游 2 类 = 5 个核对项缺件。

    合同、货权单、下游回款只进矩阵展示，不再报缺件。
    """
    diffs, matrix, _ = _diffs_for(loaded[0])
    assert len(matrix) == 10
    assert all(cell.count == 0 for cell in matrix)
    missing = [d for d in diffs if d.type == DIFF_MISSING_DOC]
    assert len(missing) == 5
    assert {(d.direction, d.role) for d in missing} == {
        (DIRECTION_UP, ROLE_SETTLEMENT), (DIRECTION_UP, ROLE_INVOICE),
        (DIRECTION_UP, ROLE_PAYMENT_RECEIPT),
        (DIRECTION_DOWN, ROLE_SETTLEMENT), (DIRECTION_DOWN, ROLE_INVOICE),
    }


def test_interest_matches_receivable(loaded):
    """差额 18000 == 应收利息 R，且 R == S，不应报利息差异。"""
    diffs, _, _ = _diffs_for(loaded[0])
    assert not [d for d in diffs if d.type == DIFF_INTEREST_MISMATCH]


def test_interest_matches_received_when_not_full(loaded):
    """差额 15000 == 实收利息 S（应收 20000 未收全）→ 只给 INFO。"""
    diffs, _, _ = _diffs_for(loaded[1])
    items = [d for d in diffs if d.type == DIFF_INTEREST_MISMATCH]
    assert len(items) == 1
    assert items[0].severity == "INFO"


def test_interest_mismatch_is_critical(loaded):
    """差额 16000，应收/实收都是 20000 → CRITICAL。"""
    diffs, _, _ = _diffs_for(loaded[2])
    items = [d for d in diffs if d.type == DIFF_INTEREST_MISMATCH]
    assert len(items) == 1
    assert items[0].severity == "CRITICAL"


def test_pay_vs_settlement_difference_is_info_only(loaded):
    """台账付款额与结算额差额属正常尾差，不能判 CRITICAL。"""
    diffs, _, _ = _diffs_for(loaded[2])
    items = [d for d in diffs if d.type == DIFF_AMOUNT_MISMATCH]
    assert items
    assert all(d.severity != "CRITICAL" for d in items)


def test_invoice_amount_mismatch_detected(loaded):
    biz = loaded[0]
    docs = [DocRecord(
        document_id=7, filename="发票.pdf", document_type="增值税发票",
        file_role="invoice", contract_no="HSYCXS-0001",
        amount=Decimal("999000"), direction=DIRECTION_DOWN, role=ROLE_INVOICE,
    )]
    diffs, matrix, _ = _diffs_for(biz, docs)
    # 发票合计低于结算金额（可能尚未开完全部发票）按提示处理，不再判 CRITICAL
    items = [d for d in diffs
             if d.type == DIFF_AMOUNT_MISMATCH and d.severity == "WARNING"
             and d.role == ROLE_INVOICE and d.direction == DIRECTION_DOWN]
    assert len(items) == 1
    # 发票格已被填充
    invoice_cell = next(c for c in matrix if c.role == ROLE_INVOICE and c.direction == DIRECTION_DOWN)
    assert invoice_cell.count == 1


def test_doc_without_contract_no_is_unlinked(loaded):
    docs = [DocRecord(document_id=9, filename="无编号.pdf", amount=Decimal("100"),
                      role=ROLE_INVOICE, direction="unknown")]
    diffs, _, unlinked = _diffs_for(loaded[0], docs)
    assert 9 in unlinked


# ---------------- 核对范围（上游三项 / 下游两项） ----------------

def test_contract_and_cargo_missing_not_reported(loaded):
    """合同、货权单不在核对范围：缺件只进矩阵，不出差异。"""
    diffs, matrix, _ = _diffs_for(loaded[0])
    missing = {(d.direction, d.role) for d in diffs if d.type == DIFF_MISSING_DOC}
    assert (DIRECTION_UP, "contract") not in missing
    assert (DIRECTION_DOWN, "contract") not in missing
    assert (DIRECTION_UP, ROLE_CARGO_RIGHT) not in missing
    assert (DIRECTION_DOWN, ROLE_CARGO_RIGHT) not in missing
    # 矩阵仍然是 2×5，资料齐套情况照常展示
    assert len(matrix) == 10


def test_up_settlement_doc_mismatch_is_critical(loaded):
    """上游结算单识别金额 ≠ 台账采购结算 → CRITICAL（数字准不准）。"""
    biz = loaded[0]   # up_settlement = 1,000,000
    docs = [DocRecord(document_id=11, filename="采购结算单.pdf", document_type="结算单",
                      contract_no="HSYCCG-0001", amount=Decimal("990000"),
                      direction=DIRECTION_UP, role=ROLE_SETTLEMENT)]
    diffs, _, _ = _diffs_for(biz, docs)
    items = [d for d in diffs
             if d.type == DIFF_AMOUNT_MISMATCH and d.role == ROLE_SETTLEMENT
             and d.direction == DIRECTION_UP]
    assert len(items) == 1
    assert items[0].severity == "CRITICAL"


def test_down_settlement_doc_mismatch_is_critical(loaded):
    """下游结算单识别金额 ≠ 台账销售结算 → CRITICAL。"""
    biz = loaded[0]   # down_settlement = 1,018,000
    docs = [DocRecord(document_id=12, filename="销售结算单.pdf", document_type="结算单",
                      contract_no="HSYCXS-0001", amount=Decimal("1010000"),
                      direction=DIRECTION_DOWN, role=ROLE_SETTLEMENT)]
    diffs, _, _ = _diffs_for(biz, docs)
    items = [d for d in diffs
             if d.type == DIFF_AMOUNT_MISMATCH and d.role == ROLE_SETTLEMENT
             and d.direction == DIRECTION_DOWN]
    assert len(items) == 1
    assert items[0].severity == "CRITICAL"


def test_upstream_refund_only_is_info_not_warning(loaded):
    """上游只有退款回单、没有货款回单：标注为「凭证未提供」，只给 INFO。"""
    biz = loaded[0]   # pay_amount = 1,000,000
    docs = [DocRecord(document_id=13, filename="退款回单.pdf", document_type="银行回单",
                      contract_no="HSYCCG-0001", amount=Decimal("18000"),
                      direction=DIRECTION_UP, role=ROLE_PAYMENT_RECEIPT,
                      nature=NATURE_REFUND)]
    diffs, _, _ = _diffs_for(biz, docs)
    items = [d for d in diffs
             if d.type == DIFF_AMOUNT_MISMATCH and d.direction == DIRECTION_UP
             and d.role == ROLE_PAYMENT_RECEIPT]
    # 两条：① 该回单是非货款款项（退款）② 货款付款凭证未提供
    assert len(items) == 2
    assert all(d.severity == "INFO" for d in items)
    target = next(d for d in items if "凭证未提供" in d.title)
    assert target.severity == "INFO"


def test_down_payment_not_checked_but_still_in_matrix(loaded):
    """下游回款不在核对范围：回单合计对不上也不出差异，矩阵照常展示。"""
    biz = loaded[0]   # receipt_amount = 1,018,000
    docs = [DocRecord(document_id=14, filename="回款回单.pdf", document_type="银行回单",
                      contract_no="HSYCXS-0001", amount=Decimal("500000"),
                      direction=DIRECTION_DOWN, role=ROLE_PAYMENT_RECEIPT,
                      nature=NATURE_PAYMENT)]
    diffs, matrix, _ = _diffs_for(biz, docs)
    assert not [d for d in diffs
                if d.direction == DIRECTION_DOWN and d.role == ROLE_PAYMENT_RECEIPT]
    cell = next(c for c in matrix
                if c.direction == DIRECTION_DOWN and c.role == ROLE_PAYMENT_RECEIPT)
    assert cell.count == 1
    assert cell.amount == 500000.0


# ---------------- run() 汇总 ----------------

def _register_source(db, path: Path) -> ExcelSource:
    src = ExcelSource(name="测试台账", file_path=str(path), enabled=True, total_sheets=4)
    db.add(src)
    db.commit()
    return src


def test_run_creates_one_report_per_business(db, ledger_path):
    _register_source(db, ledger_path)
    result = ReconciliationService(db).run()
    assert result["total"] == 3
    rows = db.query(ReconciliationReport).all()
    assert len(rows) == 3
    assert result["duration_ms"] >= 0


def test_run_without_source_raises(db):
    with pytest.raises(ReconciliationError):
        ReconciliationService(db).run()


def test_run_is_idempotent(db, ledger_path):
    _register_source(db, ledger_path)
    service = ReconciliationService(db)
    service.run()
    service.run()
    assert db.query(ReconciliationReport).count() == 3


def test_status_no_data_when_docs_missing(db, ledger_path):
    """一份单据都没上传的业务线记 no_data，不再把空矩阵当成缺件报 CRITICAL。"""
    _register_source(db, ledger_path)
    ReconciliationService(db).run()
    rows = db.query(ReconciliationReport).all()
    assert rows
    assert all(r.status == "no_data" for r in rows)


def test_no_data_line_has_note(db, ledger_path):
    _register_source(db, ledger_path)
    ReconciliationService(db).run()
    row = db.query(ReconciliationReport).first()
    payload = json.loads(row.report_json)
    assert payload.get("note")
    # 没有资料就不该凭空报"缺件"
    assert payload.get("diffs") == []
    assert row.diff_count == 0


def test_run_counts_no_data(db, ledger_path):
    """有资料的业务线仍严格判差异；无资料的单独计 no_data，不计入 error。"""
    _register_source(db, ledger_path)
    result = ReconciliationService(db).run()
    assert result["no_data"] == 3
    assert result["error"] == 0


def test_report_json_contains_matrix(db, ledger_path):
    _register_source(db, ledger_path)
    ReconciliationService(db).run()
    row = db.query(ReconciliationReport).first()
    import json
    payload = json.loads(row.report_json)
    assert len(payload["matrix"]) == 10
    assert payload["business_no"] == row.business_no


def test_unlinked_lists_documents_without_business(db, ledger_path):
    from backend.models import STATUS_PENDING
    db.add(Document(original_filename="孤立.pdf", original_path="/tmp/孤立.pdf",
                    current_filename="孤立.pdf", current_path="/tmp/孤立.pdf",
                    status=STATUS_PENDING, document_type="其他"))
    db.commit()
    _register_source(db, ledger_path)
    service = ReconciliationService(db)
    service.run()
    unlinked = service.list_unlinked()
    assert len(unlinked) == 1
    assert unlinked[0]["reason"] == "无合同号"


class TestStatementDoc:
    def test_duizhangdan_is_other_not_settlement(self):
        """业财对账单是多业务线汇总，不能判成结算单挤进结算格。"""
        assert infer_role("", "对账单", "业财对账单.pdf") == "other"
        # 真结算单即便文件名带"对账"字样也仍是结算单
        assert infer_role("", "结算单", "结算对账单.pdf") == ROLE_SETTLEMENT

    def test_statement_mentions_split_contract_no(self):
        """对账单被换行拆开、尾号斜杠简写时仍能认出本业务线编号。"""
        text = "序号 合同号 金额\n1 HSYCXS (DD)\n-2026-0200/0236/0251\n"
        variants = ["HSYCXS(DD)-2026-0236", "HSYCXS(DD)-2026-0200"]
        hits = _statement_mentions(text, variants)
        assert "HSYCXS(DD)-2026-0236" in hits

    def test_statement_mentions_exact(self):
        assert _statement_mentions("合同号 HSYCXS(DD)-2026-0236 结算", ["HSYCXS(DD)-2026-0236"])

    def test_statement_mentions_miss(self):
        assert _statement_mentions("合同号 HSYC-TZS-2026-05-12", ["HSYCXS(DD)-2026-0236"]) == []

def test_statement_doc_goes_to_statements_not_bound(db, ledger_path):
    """业财对账单不能塞进 bound：否则该业务线从 no_data 变"有资料"，
    公共 pool 被广播进来，会把别的业务线的发票/回单抢过来。"""
    from backend.models import STATUS_PENDING
    db.add(Document(original_filename="业财对账单.pdf", original_path="/tmp/stmt.pdf",
                    current_filename="业财对账单.pdf", current_path="/tmp/stmt.pdf",
                    status=STATUS_PENDING, document_type="对账单",
                    extracted_text="合同号 HSYCXS-0001 与 HSYCXS-0002"))
    db.commit()
    _register_source(db, ledger_path)

    svc = ReconciliationService(db)
    src = svc.resolve_source(None)
    reader = LedgerReader(src.file_path)
    try:
        businesses = reader.load()
    finally:
        reader.close()
    docs = _load_documents(db)
    bound, _dup_ids, _pool, statements = svc._bind_documents(businesses, docs)

    assert all(not any(d.role == "other" for d in v) for v in bound.values())
    assert "YWX001" in statements and "YWX002" in statements

    result = svc.run()
    assert result["error"] == 0
    assert result["unlinked"] == 0

def test_norm_no_strips_trailing_bracket_only():
    """合同正文自写编号尾部的残留括号要剥掉，中间的 (DD) 要保留。"""
    assert norm_no("HSYC-TZS-2026-05-12）") == "HSYC-TZS-2026-05-12"
    assert norm_no("HSYCXS（DD）-2026-0236") == "HSYCXS(DD)-2026-0236"
