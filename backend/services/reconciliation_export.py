"""对账结果导出为 Excel（P0-1）。

为什么单独一个模块：
- 导出是"读快照 + 排版"，不参与对账判定，放在 reconciliation_service 里会让
  那个已经 1700+ 行的文件继续膨胀，也会让导出逻辑夹在规则里难以测试；
- 导出只依赖 `ReconciliationReport.report_json` 这一个契约，判定层怎么改都不影响排版。

四个 sheet：
1. 对账明细 —— 一行一条业务线（台账口径 + 金额 + 状态）
2. 差异明细 —— 一行一条差异（类型/严重级别/应有/实际/差额/关联单据/处理情况）
3. 待补资料 —— 只列缺件格，可直接拿去催单
4. 未归属单据 —— 有合同号但挂不到业务线的资料
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Iterable, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

STATUS_LABELS = {
    "ok": "已对齐",
    "warning": "有提示",
    "error": "有错误",
    "no_data": "未上传资料",
}

ROLE_LABELS = {
    "contract": "合同",
    "settlement": "结算单",
    "invoice": "发票",
    "cargo_right": "货权单",
    "payment_receipt": "收付款回单",
    "other": "其他",
}

DIR_LABELS = {"up": "上游", "down": "下游", "unknown": "未定"}

DIFF_LABELS = {
    "MISSING_DOC": "单据缺失",
    "AMOUNT_MISMATCH": "金额不符",
    "INTEREST_MISMATCH": "利息不符",
    "CHAIN_BREAK": "合同号不属于本业务线",
    "DATE_CONFLICT": "日期时序倒置",
    "DUPLICATE_BIZ": "重复单据/重复记账",
    "LEDGER_MISSING": "台账缺该业务线",
    "UNPARSED_AMOUNT": "金额未识别",
    "UNCLASSIFIED_DOC": "单据无法归类",
}

SEVERITY_LABELS = {"CRITICAL": "错误", "WARNING": "提示", "INFO": "说明"}

RESOLUTION_LABELS = {
    "ignore": "已忽略",
    "false_positive": "误报",
    "supplement": "待补单",
    "adjust": "待调账",
    "pending": "待确认",
}

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
_BODY_FONT = Font(size=10)
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_MONEY_FMT = "#,##0.00"


def _num(value) -> Optional[float]:
    """payload 里的金额是字符串（可能带千分位/空串），统一转 float 供写单元格。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except Exception:
        return None


def _sheet(wb: Workbook, title: str, headers: list[str], widths: list[int]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _BORDER
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22
    return ws


def _finish(ws, money_cols: Iterable[int] = ()) -> None:
    """统一正文样式：字号、边框、自动换行（长说明列），金额列千分位。"""
    money = set(money_cols)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = _BODY_FONT
            cell.border = _BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.column in money:
                cell.number_format = _MONEY_FMT


def build_workbook(rows: Iterable) -> io.BytesIO:
    """把对账快照列表排成 4 个 sheet 的 xlsx，返回内存流。"""
    wb = Workbook()
    wb.remove(wb.active)

    # ---- 1. 对账明细 ----
    ws1 = _sheet(
        wb, "对账明细",
        ["业务线号", "台账Sheet", "供应商", "采购商", "船名", "货种",
         "上游合同号", "下游合同号", "上游结算", "下游结算",
         "应收利息", "实收利息", "台账付款", "台账收款",
         "差异条数", "状态", "核对时间", "备注"],
        [20, 14, 28, 24, 10, 10, 22, 22, 15, 15, 12, 12, 14, 14, 9, 12, 18, 40],
    )
    for row in rows:
        payload = json.loads(row.report_json or "{}")
        ws1.append([
            row.business_no,
            row.ledger_sheet,
            payload.get("supplier", ""),
            payload.get("customer", ""),
            payload.get("vessel", ""),
            payload.get("material", ""),
            row.up_contract_no,
            row.down_contract_no,
            _num(payload.get("up_settlement")),
            _num(payload.get("down_settlement")),
            _num(payload.get("interest_receivable")),
            _num(payload.get("interest_received")),
            _num(payload.get("pay_amount")),
            _num(payload.get("receipt_amount")),
            row.diff_count,
            STATUS_LABELS.get(row.status, row.status),
            row.checked_at.strftime("%Y-%m-%d %H:%M") if row.checked_at else "",
            payload.get("note", ""),
        ])
    _finish(ws1, money_cols=(9, 10, 11, 12, 13, 14))

    # ---- 2. 差异明细 ----
    ws2 = _sheet(
        wb, "差异明细",
        ["业务线号", "差异类型", "方向", "环节", "严重级别", "标题",
         "应有值", "实际值", "差额", "说明", "关联单据ID", "处理情况", "是否新增/遗留"],
        [20, 18, 8, 14, 10, 30, 16, 16, 14, 50, 22, 12, 14],
    )
    for row in rows:
        payload = json.loads(row.report_json or "{}")
        for diff in payload.get("diffs", []):
            resolution = diff.get("resolution") or {}
            res_label = RESOLUTION_LABELS.get(resolution.get("type", ""), "")
            if not res_label and diff.get("ignored"):
                res_label = "已忽略"
            note = resolution.get("note", "")
            if note:
                res_label = f"{res_label}（{note}）" if res_label else note
            ws2.append([
                row.business_no,
                DIFF_LABELS.get(diff.get("type", ""), diff.get("type", "")),
                DIR_LABELS.get(diff.get("direction", ""), diff.get("direction", "")),
                ROLE_LABELS.get(diff.get("role", ""), diff.get("role", "")),
                SEVERITY_LABELS.get(diff.get("severity", ""), diff.get("severity", "")),
                diff.get("title", ""),
                diff.get("expected", ""),
                diff.get("actual", ""),
                diff.get("delta", ""),
                diff.get("message", ""),
                ",".join(str(i) for i in diff.get("document_ids", [])),
                res_label,
                "新增" if diff.get("is_new") else ("遗留" if diff.get("is_new") is False else ""),
            ])
    _finish(ws2)

    # ---- 3. 待补资料（缺件格）----
    ws3 = _sheet(
        wb, "待补资料",
        ["业务线号", "方向", "环节", "台账已登记金额", "严重级别", "说明", "最后催办时间", "催办次数"],
        [20, 8, 14, 16, 10, 46, 18, 10],
    )
    for row in rows:
        payload = json.loads(row.report_json or "{}")
        urges = payload.get("urges") or []
        last_urge = urges[-1].get("at", "") if urges else ""
        for diff in payload.get("diffs", []):
            if diff.get("type") != "MISSING_DOC":
                continue
            ws3.append([
                row.business_no,
                DIR_LABELS.get(diff.get("direction", ""), diff.get("direction", "")),
                ROLE_LABELS.get(diff.get("role", ""), diff.get("role", "")),
                _num(diff.get("expected")),
                SEVERITY_LABELS.get(diff.get("severity", ""), diff.get("severity", "")),
                diff.get("message", ""),
                last_urge,
                len(urges),
            ])
    _finish(ws3, money_cols=(4,))

    # ---- 4. 未归属单据 ----
    ws4 = _sheet(
        wb, "未归属单据",
        ["业务线号", "单据ID"],
        [22, 12],
    )
    for row in rows:
        payload = json.loads(row.report_json or "{}")
        for doc_id in payload.get("unlinked_document_ids", []):
            ws4.append([row.business_no, doc_id])
    _finish(ws4)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_filename(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return f"reconciliation_{now.strftime('%Y%m%d_%H%M')}.xlsx"
