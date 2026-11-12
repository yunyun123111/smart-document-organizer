"""业务线台账自动对账服务（V6）。

职责：
1. 读台账 4 个 sheet（云创总合同 / 物流合同 / 结算数据 / 宏达收付款台账新），
   按「业务线号 → 销售合同号」跨表关联，得到每条业务线的合同、结算、利息、收付款口径。
2. 把已归档文档按上下游合同号归位到业务线，并判定方向与单据角色。
3. 跑 R1–R7 七条规则，产出差异清单。
4. 结果 upsert 到 reconciliation_reports（一条业务线一行）。

设计约束：
- 不改 business_records 主键（仍按合同号），对账结果独立成表；
- 台账列按表头名识别，识别不到时用固定列字母兜底；
- 金额一律 Decimal，容差 0.01 元；
- 金额识别失败不按 0 计算，单独报 UNPARSED_AMOUNT。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import (
    DIFF_AMOUNT_MISMATCH,
    DIFF_CHAIN_BREAK,
    DIFF_DATE_CONFLICT,
    DIFF_DUPLICATE_BIZ,
    DIFF_INTEREST_MISMATCH,
    DIFF_LEDGER_MISSING,
    DIFF_MISSING_DOC,
    DIFF_UNCLASSIFIED_DOC,
    DIFF_UNPARSED_AMOUNT,
    RECON_STATUS_ERROR,
    RECON_STATUS_NO_DATA,
    RECON_STATUS_OK,
    RECON_STATUS_WARNING,
    RECON_TOLERANCE,
    BusinessFile,
    BusinessRecord,
    Document,
    DocumentField,
    ExcelSource,
    ReconciliationReport,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_RECYCLED,
)
from backend.services.doc_dedup import fingerprint as doc_fingerprint
from backend.services.doc_dedup import is_same_image
from backend.utils.logger import get_logger

logger = get_logger("services.reconciliation")

# ---- 台账 sheet 名 ----
# 对账主数据只取「云创总合同」；「物流合同」是同一批业务线的另一套编号记账
# （用户用于区分陕建线业务统计），不参与金额对账，仅补充 SJWL 合同号供文档归位。
MAIN_CONTRACT_SHEET = "云创总合同"
AUX_CONTRACT_SHEET = "物流合同"
CONTRACT_SHEETS = (MAIN_CONTRACT_SHEET,)
SETTLEMENT_SHEET = "结算数据"
PAYMENT_SHEET = "宏达收付款台账新"

# ---- 单据角色（对账用，按 5 类 × 2 方向）----
ROLE_CONTRACT = "contract"
ROLE_SETTLEMENT = "settlement"
ROLE_INVOICE = "invoice"
ROLE_CARGO_RIGHT = "cargo_right"
ROLE_PAYMENT_RECEIPT = "payment_receipt"
RECON_ROLES: tuple[str, ...] = (
    ROLE_CONTRACT,
    ROLE_SETTLEMENT,
    ROLE_INVOICE,
    ROLE_CARGO_RIGHT,
    ROLE_PAYMENT_RECEIPT,
)
ROLE_LABELS = {
    ROLE_CONTRACT: "合同",
    ROLE_SETTLEMENT: "结算单",
    ROLE_INVOICE: "发票",
    ROLE_CARGO_RIGHT: "货权单/出库单",
    ROLE_PAYMENT_RECEIPT: "收付款回单",
}

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_UNKNOWN = "unknown"

# 合同号前缀 → 方向（两套编号体系都覆盖）
UP_PREFIXES = ("HSYCCG", "SJWLCG", "BOTC", "CG")
DOWN_PREFIXES = ("HSYCXS", "SJWLXS", "SOTC", "XS")


class ReconciliationError(Exception):
    """对账业务异常（台账未登记、文件不可读等）。"""


# ==================== 基础类型 ====================

@dataclass
class LedgerBusiness:
    """台账里的一条业务线（主表口径 + 关联到的结算/收付款口径）。"""

    business_no: str
    ledger_sheet: str = ""
    up_contract_no: str = ""
    down_contract_no: str = ""
    up_alt_no: str = ""
    down_alt_no: str = ""
    # 同一业务线在两套编号体系（HSYC / SJWL）下各登记一次，这里聚合全部合同号用于文档匹配
    up_contract_nos: list[str] = field(default_factory=list)
    down_contract_nos: list[str] = field(default_factory=list)
    up_contract_amount: Optional[Decimal] = None
    down_contract_amount: Optional[Decimal] = None
    up_contract_date: Optional[date] = None
    down_contract_date: Optional[date] = None
    supplier: str = ""
    customer: str = ""
    vessel: str = ""
    material: str = ""
    # 结算数据
    up_settlement: Optional[Decimal] = None
    down_settlement: Optional[Decimal] = None
    settlement_interest_received: Optional[Decimal] = None
    # 宏达收付款台账
    interest_receivable: Optional[Decimal] = None
    interest_received: Optional[Decimal] = None
    pay_amount: Optional[Decimal] = None
    receipt_amount: Optional[Decimal] = None
    invoice_date_ledger: Optional[date] = None
    settlement_missing: bool = False   # 结算数据里找不到该销售合同
    payment_missing: bool = False      # 收付款台账里找不到该销售合同


@dataclass
class DocRecord:
    """归位到业务线的一份文档。"""

    document_id: int
    filename: str = ""
    document_type: str = ""
    file_role: str = ""
    contract_no: str = ""
    amount: Optional[Decimal] = None
    amount_raw: str = ""
    doc_date: Optional[date] = None
    invoice_no: str = ""
    company: str = ""
    seller: str = ""
    direction: str = DIRECTION_UNKNOWN
    role: str = "other"
    # 归属依据：contract / party / amount，空串表示仍按合同号未归属
    assigned_by: str = ""
    # 款项性质：payment（货款）/ deposit（保证金·押金）/ refund（退款）。
    # 一张回单的金额是否计入「回款/付款合计」取决于性质，不能一律相加。
    nature: str = "payment"
    # 正文（前 3000 字）：货权单/放货通知没有合同号也没有金额，
    # 判上下游只能读正文里的「货权转移给谁」和船名。
    text: str = ""
    # 图像指纹（dHash, 宽, 高）：判「同一张图被上传成两份副本」。
    # 业务键去重依赖抽出来的合同号/金额/日期，只要任一项抽错就失效；
    # 指纹只看图像本身，能兜住这种情况。
    phash: str = ""
    img_w: int = 0
    img_h: int = 0


@dataclass
class DiffItem:
    type: str
    severity: str
    title: str
    direction: str = ""
    role: str = ""
    expected: str = ""
    actual: str = ""
    delta: str = ""
    message: str = ""
    document_ids: list[int] = field(default_factory=list)
    candidate_document_ids: list[int] = field(default_factory=list)
    ignored: bool = False

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "title": self.title,
            "direction": self.direction,
            "role": self.role,
            "expected": self.expected,
            "actual": self.actual,
            "delta": self.delta,
            "message": self.message,
            "document_ids": list(self.document_ids),
            "candidate_document_ids": list(self.candidate_document_ids),
            "ignored": bool(self.ignored),
        }


# ==================== 通用工具 ====================

_ZERO = Decimal("0")


def norm_text(value) -> str:
    """表头 / 编号规范化：去空格、全角括号转半角、小写。"""
    if value is None:
        return ""
    s = str(value).strip()
    s = s.replace("\u3000", " ").replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def norm_no(value) -> str:
    """合同号规范化（比对用）：去空格、全角转半角、剥尾部残留括号、大写。"""
    if value is None:
        return ""
    s = str(value).strip()
    s = s.replace("\u3000", " ").replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    # 合同正文里的自写编号常带一个收尾括号（"HSYC-TZS-2026-05-12）"），那是
    # 排版残留不是编号本身；只剥尾部，中间的 (DD) 必须保留。
    s = s.rstrip(")")
    return s.upper()


def contract_variants(value) -> set[str]:
    """合同号的等价写法：原样、去掉括号内容。"""
    base = norm_no(value)
    if not base:
        return set()
    out = {base}
    out.add(re.sub(r"\([^)]*\)", "", base))
    return {v for v in out if v}


def to_decimal(value) -> Optional[Decimal]:
    """把台账/单据里的金额转 Decimal。转不了返回 None（不返回 0）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    s = s.replace("元", "").replace("\u3000", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):  # 会计负数写法
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def to_date(value) -> Optional[date]:
    """日期解析：datetime/date / 字符串 / Excel 序列号。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # Excel 序列号（1899-12-30 起算）
        if 20000 < float(value) < 80000:
            try:
                return (datetime(1899, 12, 30) + timedelta(days=int(value))).date()
            except OverflowError:
                return None
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_amount(value: Optional[Decimal]) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


def _all_contract_nos(biz: LedgerBusiness) -> list[str]:
    """业务线名下的全部合同号（含两套编号体系、2.0 编号）。"""
    nos = list(biz.up_contract_nos) + list(biz.down_contract_nos)
    for raw in (biz.up_contract_no, biz.down_contract_no, biz.up_alt_no, biz.down_alt_no):
        if raw and raw not in nos:
            nos.append(raw)
    return nos


def _variants_of(nos: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for no in nos:
        out |= contract_variants(no)
    return out


def _expand_index(mapping: dict[str, dict]) -> dict[str, dict]:
    """把以合同号为键的映射展开成「含去括号变体」的索引，容忍括号写法差异。"""
    out: dict[str, dict] = {}
    for key, value in mapping.items():
        for variant in contract_variants(key):
            out.setdefault(variant, value)
    return out


# ==================== 台账读取 ====================

class LedgerReader:
    """把一个台账 xlsx 读成 LedgerBusiness 列表。"""

    def __init__(self, path: str) -> None:
        self.path = path
        p = Path(path)
        if not p.exists():
            raise ReconciliationError(f"台账文件不存在：{path}")
        try:
            self.wb = load_workbook(str(p), data_only=True)
        except Exception as exc:  # noqa: BLE001
            raise ReconciliationError(f"台账文件无法打开：{exc}") from exc

    def close(self) -> None:
        try:
            self.wb.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- sheet 定位 ----
    def _sheet(self, name: str):
        if name in self.wb.sheetnames:
            return self.wb[name]
        # 兜底：包含关键字
        for sn in self.wb.sheetnames:
            if name in sn or sn in name:
                return self.wb[sn]
        return None

    @staticmethod
    def _headers(ws) -> list[str]:
        row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        return [norm_text(c) for c in row]

    @staticmethod
    def _find(headers: list[str], aliases: Iterable[str], start: int = 0) -> Optional[int]:
        wanted = {norm_text(a) for a in aliases}
        for idx in range(start, len(headers)):
            if headers[idx] in wanted:
                return idx
        return None

    @staticmethod
    def _cell(row: tuple, idx: Optional[int]):
        if idx is None or idx < 0 or idx >= len(row):
            return None
        return row[idx]

    # ---- 主表（云创总合同 / 物流合同）----
    def _read_contract_sheet(self, ws) -> list[LedgerBusiness]:
        headers = self._headers(ws)
        i_biz = self._find(headers, ["业务线", "业务线号", "业务编号"])
        i_up = self._find(headers, ["采购合同编号", "采购合同号", "采购合同编号oa"])
        i_down = self._find(headers, ["销售合同编号oa", "销售合同编号", "销售合同号"])
        i_up_amount = self._find(headers, ["采购合同金额", "采购金额"])
        i_down_amount = self._find(headers, ["销售合同金额", "销售金额"])
        i_up_date = self._find(headers, ["采购合同签订日期", "采购日期"])
        i_down_date = self._find(headers, ["销售合同签订日期", "销售日期"])
        i_supplier = self._find(headers, ["供应商"])
        i_customer = self._find(headers, ["采购商名称", "销售客户", "采购商"])
        i_vessel = self._find(headers, ["船名"])
        i_material = self._find(headers, ["物料名称", "物料"])

        # 「2.0编号」在表里出现两次，按锚点列右侧最近一个定位
        def alt_after(anchor: Optional[int]) -> Optional[int]:
            if anchor is None:
                return None
            for idx in range(anchor + 1, len(headers)):
                if headers[idx] == "2.0编号":
                    return idx
                if headers[idx] in ("业务线", "采购合同签订日期", "销售合同签订日期"):
                    break
            return None

        i_up_alt = alt_after(i_up)
        i_down_alt = alt_after(i_down)

        out: list[LedgerBusiness] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            biz = norm_no(self._cell(row, i_biz))
            if not biz:
                continue
            up_no = norm_no(self._cell(row, i_up))
            down_no = norm_no(self._cell(row, i_down))
            up_alt = norm_no(self._cell(row, i_up_alt))
            down_alt = norm_no(self._cell(row, i_down_alt))
            out.append(LedgerBusiness(
                business_no=biz,
                ledger_sheet=ws.title,
                up_contract_no=up_no,
                down_contract_no=down_no,
                up_alt_no=up_alt,
                down_alt_no=down_alt,
                up_contract_nos=[n for n in (up_no, up_alt) if n],
                down_contract_nos=[n for n in (down_no, down_alt) if n],
                up_contract_amount=to_decimal(self._cell(row, i_up_amount)),
                down_contract_amount=to_decimal(self._cell(row, i_down_amount)),
                up_contract_date=to_date(self._cell(row, i_up_date)),
                down_contract_date=to_date(self._cell(row, i_down_date)),
                supplier=str(self._cell(row, i_supplier) or "").strip(),
                customer=str(self._cell(row, i_customer) or "").strip(),
                vessel=str(self._cell(row, i_vessel) or "").strip(),
                material=str(self._cell(row, i_material) or "").strip(),
            ))
        return out

    # ---- 结算数据 ----
    def _read_settlement_sheet(self, ws) -> dict[str, dict]:
        headers = self._headers(ws)
        i_key = self._find(headers, ["销售合同编号oa", "销售合同编号"])
        i_total = self._find(headers, ["合计"])
        i_up = self._find(headers, ["采购结算金额"])
        i_tail = self._find(headers, ["尾数"])
        i_tail_inv = self._find(headers, ["尾票"])
        i_down = self._find(headers, ["销售结算金额"])
        i_interest = self._find(headers, ["实收利息"])

        out: dict[str, dict] = {}
        if i_key is None:
            return out
        for row in ws.iter_rows(min_row=2, values_only=True):
            key = norm_no(self._cell(row, i_key))
            if not key:
                continue
            up = to_decimal(self._cell(row, i_up))
            total = to_decimal(self._cell(row, i_total))
            if total is None and up is not None:
                total = up
                for idx in (i_tail, i_tail_inv):
                    v = to_decimal(self._cell(row, idx))
                    if v is not None:
                        total += v
            bucket = out.setdefault(key, {
                "up_settlement": _ZERO, "down_settlement": _ZERO,
                "interest": _ZERO, "rows": 0, "has_value": False,
            })
            if total is not None:
                bucket["up_settlement"] += total
                bucket["has_value"] = True
            down = to_decimal(self._cell(row, i_down))
            if down is not None:
                bucket["down_settlement"] += down
            interest = to_decimal(self._cell(row, i_interest))
            if interest is not None:
                bucket["interest"] += interest
            bucket["rows"] += 1
        return out

    # ---- 宏达收付款台账新 ----
    def _read_payment_sheet(self, ws) -> dict[str, dict]:
        headers = self._headers(ws)
        i_key = self._find(headers, ["销售合同编号", "销售合同编号oa"])
        i_up = self._find(headers, ["采购合同编号"])
        i_pay = self._find(headers, ["支付金额"])
        i_receipt = self._find(headers, ["回款金额"])
        i_recv_int = self._find(headers, ["应收利息"])
        i_real_int = self._find(headers, ["实收利息"])
        i_inv_date = self._find(headers, ["销售开票日期"])

        out: dict[str, dict] = {}
        if i_key is None:
            return out
        for row in ws.iter_rows(min_row=2, values_only=True):
            key = norm_no(self._cell(row, i_key))
            if not key:
                continue
            bucket = out.setdefault(key, {
                "up_contract_no": "",
                "pay_amount": _ZERO, "receipt_amount": _ZERO,
                "interest_receivable": _ZERO, "interest_received": _ZERO,
                "invoice_date": None, "rows": 0, "has_value": False,
            })
            for k, idx in (
                ("pay_amount", i_pay),
                ("receipt_amount", i_receipt),
                ("interest_receivable", i_recv_int),
                ("interest_received", i_real_int),
            ):
                v = to_decimal(self._cell(row, idx))
                if v is not None:
                    bucket[k] += v
                    bucket["has_value"] = True
            if not bucket["up_contract_no"]:
                bucket["up_contract_no"] = norm_no(self._cell(row, i_up))
            d = to_date(self._cell(row, i_inv_date))
            if d and (bucket["invoice_date"] is None or d < bucket["invoice_date"]):
                bucket["invoice_date"] = d
            bucket["rows"] += 1
        return out

    # ---- 汇总 ----
    def load(self) -> list[LedgerBusiness]:
        main_ws = self._sheet(MAIN_CONTRACT_SHEET)
        if main_ws is None:
            raise ReconciliationError(f"台账缺少「{MAIN_CONTRACT_SHEET}」sheet，无法对账")
        raw = self._read_contract_sheet(main_ws)

        # 阶段一：同业务线多行累加（多船期）
        merged: dict[str, LedgerBusiness] = {}
        for b in raw:
            prev = merged.get(b.business_no)
            if prev is None:
                merged[b.business_no] = b
                continue
            if b.up_contract_amount is not None:
                prev.up_contract_amount = (prev.up_contract_amount or _ZERO) + b.up_contract_amount
            if b.down_contract_amount is not None:
                prev.down_contract_amount = (prev.down_contract_amount or _ZERO) + b.down_contract_amount
            for attr in ("up_contract_no", "down_contract_no", "supplier", "customer", "vessel", "material"):
                if not getattr(prev, attr) and getattr(b, attr):
                    setattr(prev, attr, getattr(b, attr))
            for attr in ("up_contract_nos", "down_contract_nos"):
                target = getattr(prev, attr)
                for value in getattr(b, attr):
                    if value not in target:
                        target.append(value)
            if prev.up_contract_date is None:
                prev.up_contract_date = b.up_contract_date
            if prev.down_contract_date is None:
                prev.down_contract_date = b.down_contract_date

        # 阶段二：物流合同只补编号，不新增业务线、不参与金额
        aux_ws = self._sheet(AUX_CONTRACT_SHEET)
        if aux_ws is not None:
            for aux in self._read_contract_sheet(aux_ws):
                target = merged.get(aux.business_no)
                if target is None:
                    continue
                for attr in ("up_contract_nos", "down_contract_nos"):
                    target_list = getattr(target, attr)
                    for value in getattr(aux, attr):
                        if value not in target_list:
                            target_list.append(value)

        ws_settle = self._sheet(SETTLEMENT_SHEET)
        settle_map = _expand_index(self._read_settlement_sheet(ws_settle)) if ws_settle is not None else {}
        ws_pay = self._sheet(PAYMENT_SHEET)
        pay_map = _expand_index(self._read_payment_sheet(ws_pay)) if ws_pay is not None else {}

        for biz in merged.values():
            keys = contract_variants(biz.down_contract_no) | contract_variants(biz.down_alt_no)
            settle = None
            for k in keys:
                if k in settle_map:
                    settle = settle_map[k]
                    break
            pay = None
            for k in keys:
                if k in pay_map:
                    pay = pay_map[k]
                    break

            if settle is None:
                biz.settlement_missing = True
            else:
                biz.up_settlement = settle["up_settlement"]
                biz.down_settlement = settle["down_settlement"]
                biz.settlement_interest_received = settle["interest"]

            if pay is None:
                biz.payment_missing = True
            else:
                biz.pay_amount = pay["pay_amount"]
                biz.receipt_amount = pay["receipt_amount"]
                biz.interest_receivable = pay["interest_receivable"]
                biz.interest_received = pay["interest_received"]
                biz.invoice_date_ledger = pay["invoice_date"]
                if not biz.up_contract_no and pay["up_contract_no"]:
                    biz.up_contract_no = pay["up_contract_no"]

        return list(merged.values())


# ==================== 文档归位 ====================

_FIELD_ALIASES = {
    "contract_no": ("contract_no", "合同编号", "合同号"),
    "amount": ("amount", "金额", "合同金额", "发票金额", "价税合计"),
    "date": ("date", "日期", "合同日期", "签订日期", "开票日期"),
    "invoice_no": ("invoice_no", "发票号", "发票号码"),
    # 对手方（发票购买方/销售方、回单户名）—— 无合同号的单据靠公司名判定上下游
    "company": ("company", "购买方", "购买方名称", "买方", "采购商", "付款人", "户名"),
    "seller": ("seller", "销售方", "销售方名称", "销方", "卖方", "供应商"),
    "summary": ("summary", "摘要", "款项性质", "用途", "附言", "交易描述"),
}

_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ROLE_PAYMENT_RECEIPT, ("回单", "水单", "付款凭证", "收款凭证", "银行", "payment", "receipt")),
    (ROLE_CARGO_RIGHT, ("出库", "货权", "提单", "放货", "入库", "cargo", "waybill")),
    (ROLE_INVOICE, ("发票", "invoice")),
    (ROLE_SETTLEMENT, ("结算", "对账", "磅单", "结算单", "settlement")),
    (ROLE_CONTRACT, ("合同", "协议", "contract", "agreement")),
)


def infer_role(file_role: str, document_type: str, filename: str) -> str:
    """角色判定：文件角色优先，其次文档类型/文件名关键词。"""
    role = (file_role or "").strip().lower()
    role_map = {
        "contract": ROLE_CONTRACT,
        "settlement": ROLE_SETTLEMENT,
        "invoice": ROLE_INVOICE,
        "cargo_right": ROLE_CARGO_RIGHT,
        "payment_receipt": ROLE_PAYMENT_RECEIPT,
        "contract_up": ROLE_CONTRACT,
        "contract_down": ROLE_CONTRACT,
    }
    if role in role_map:
        return role_map[role]

    text = f"{document_type or ''} {filename or ''}".lower()
    # 业财对账单：一份文档里合并了多条业务线的对账汇总（正文能看到
    # HSYCXS(DD)-2026-0200/0236/0251 这样的编号列表）。它既不是上游结算单
    # 也不是下游结算单，判成 settlement 会挤进结算格、还会报「无法判定上下游」。
    # 这类单据单独归为 other：不进矩阵、不再报未分类，由专用提示说明归属。
    if "对账" in text and "结算单" not in text:
        return "other"
    for candidate, keywords in _ROLE_KEYWORDS:
        if any(kw.lower() in text for kw in keywords):
            return candidate
    return "other"


def infer_direction(file_role: str, contract_no: str) -> str:
    """方向判定：显式角色 → 合同号前缀。"""
    role = (file_role or "").strip().lower()
    if role == "contract_up":
        return DIRECTION_UP
    if role == "contract_down":
        return DIRECTION_DOWN
    no = norm_no(contract_no)
    if not no:
        return DIRECTION_UNKNOWN
    if any(no.startswith(p) for p in UP_PREFIXES):
        return DIRECTION_UP
    if any(no.startswith(p) for p in DOWN_PREFIXES):
        return DIRECTION_DOWN
    return DIRECTION_UNKNOWN


NATURE_PAYMENT = "payment"
NATURE_DEPOSIT = "deposit"
NATURE_REFUND = "refund"

_DEPOSIT_KEYWORDS = ("保证金", "押金", "定金", "订金", "履约金")
_REFUND_KEYWORDS = ("退款", "退回", "退还", "返还", "退货款")


def infer_nature(filename: str, summary: str, document_type: str) -> str:
    """判断一张收付款凭证的款项性质。

    保证金/押金不是货款，退款是对已付款的冲减，两者都不能并进「回款/付款
    合计」：一张 59.6 万保证金回单会把下游回款总额抬到 739 万，进而误判
    成与台账 675.75 万不符（差 63.68 万恰好就是保证金）。所以先按摘要和
    文件名分出性质，再决定是否参与金额勾稽。
    """
    text = norm_text(f"{filename} {summary} {document_type}")
    if any(k in text for k in _DEPOSIT_KEYWORDS):
        return NATURE_DEPOSIT
    if any(k in text for k in _REFUND_KEYWORDS):
        return NATURE_REFUND
    return NATURE_PAYMENT


def _load_documents(db: Session) -> list[DocRecord]:
    """一次性加载全部有效文档 + 关键字段 + 业务档案角色。"""
    docs = db.execute(
        select(Document).where(Document.status != STATUS_RECYCLED)
    ).scalars().all()
    if not docs:
        return []

    field_rows = db.execute(
        select(DocumentField.document_id, DocumentField.field_name, DocumentField.field_value)
        .order_by(DocumentField.created_at.asc(), DocumentField.id.asc())
    ).all()

    wanted: dict[int, dict[str, str]] = {}
    alias_lookup = {norm_text(a): key for key, aliases in _FIELD_ALIASES.items() for a in aliases}
    for doc_id, name, value in field_rows:
        key = alias_lookup.get(norm_text(name))
        if not key:
            continue
        # 同一字段可能有多行（重跑写入新值、人工补录值仍保留）：按 created_at/id
        # 升序遍历，后写覆盖先写 —— 取最新一行才是当前识别结果
        bucket = wanted.setdefault(doc_id, {})
        if value not in (None, ""):
            bucket[key] = str(value)

    # 档案角色（合同号级档案）
    role_rows = db.execute(
        select(BusinessFile.document_id, BusinessFile.file_role, BusinessRecord.business_no)
        .join(BusinessRecord, BusinessRecord.id == BusinessFile.business_id)
    ).all()
    role_map: dict[int, tuple[str, str]] = {}
    for doc_id, file_role, business_no in role_rows:
        if doc_id not in role_map:
            role_map[doc_id] = (file_role or "", business_no or "")

    out: list[DocRecord] = []
    for doc in docs:
        fields = wanted.get(doc.id, {})
        file_role, linked_no = role_map.get(doc.id, ("", ""))
        contract_no = fields.get("contract_no") or linked_no
        fname = doc.current_filename or doc.original_filename or ""
        role = infer_role(file_role, doc.document_type, fname)
        fp = doc_fingerprint(doc.current_path or doc.original_path)
        out.append(DocRecord(
            document_id=doc.id,
            filename=fname,
            document_type=doc.document_type or "",
            file_role=file_role,
            contract_no=norm_no(contract_no),
            amount=to_decimal(fields.get("amount")),
            amount_raw=fields.get("amount", ""),
            doc_date=to_date(fields.get("date")),
            invoice_no=norm_no(fields.get("invoice_no")),
            company=fields.get("company", ""),
            seller=fields.get("seller", ""),
            role=role,
            direction=infer_direction(file_role, contract_no),
            nature=infer_nature(fname, fields.get("summary", ""), doc.document_type or ""),
            text=(doc.extracted_text or "")[:3000],
            phash=fp[0] if fp else "",
            img_w=fp[1] if fp else 0,
            img_h=fp[2] if fp else 0,
        ))
    return out


def _same_image_record(a: DocRecord, b: DocRecord) -> bool:
    """两条记录是否来自同一张图（两份副本）。

    判据：dHash 距离 ≤ 1 位且像素尺寸完全相同（见 doc_dedup）。另加一道保险
    —— 两边金额都抽出来了但数值不同时不算重复，避免「同版式不同金额」被误合并。
    """
    if not a.phash or not b.phash:
        return False
    if (a.amount is not None and b.amount is not None
            and abs(a.amount - b.amount) >= _AMOUNT_TOL):
        return False
    return is_same_image((a.phash, a.img_w, a.img_h), (b.phash, b.img_w, b.img_h))


def _statement_mentions(text: str, variants: Iterable[str]) -> list[str]:
    """对账单正文是否提到这些合同号，返回命中的编号列表。

    对账单是按业务线合并的汇总表，正文里常写成
    「HSYCXS (DD)\\n-2026-0200/0236/0251」（被换行拆开、尾号用斜杠简写），
    所以先把空白全部去掉，再逐个编号比对；完整编号找不到时，退一步看
    「-年份-」后面同一段数字列表里有没有这个尾号。
    """
    compact = re.sub(r"\s+", "", text or "")
    hits: list[str] = []
    for v in variants:
        if not v:
            continue
        if v in compact:
            hits.append(v)
            continue
        parts = v.split("-")
        if len(parts) < 3:
            continue
        year, tail = parts[-2], parts[-1]
        if not tail:
            continue
        # 尾号前 40 字符必须都落在 [数字/斜杠] 内，确保是同一串编号列表而不是金额
        if re.search(rf"-{year}-[0-9/]{{0,40}}{re.escape(tail)}(?![0-9])", compact):
            hits.append(v)
    return hits


# ---------- 归属：合同号 → 对手方公司名 → 金额锚点 ----------
#
# 发票只有票号、回单只有流水号、货权单只有船名和吨位，这些单据本身没有
# 合同号字段，只按合同号认业务线会让它们全部掉进「未归属」并在矩阵里显示
# 成缺件。这里按证据强度逐级降级：公司名（强）→ 单份金额（强）→
# 同角色多份合计金额（分笔付款/回款，较弱但可解释）。

_AMOUNT_TOL = Decimal("0.01")

_NAME_NOISE = (
    "股份有限公司", "有限责任公司", "有限公司", "国际贸易", "贸易", "钢铁",
)


def _name_core(value) -> str:
    """公司名核心字号：去括号内容与常见后缀，用于容忍 OCR 的全半角/简称差异。"""
    s = norm_text(value)
    if not s:
        return ""
    s = re.sub(r"\([^)]*\)", "", s)
    for word in _NAME_NOISE:
        s = s.replace(word, "")
    return s


def _name_match(value, target) -> bool:
    core_v, core_t = _name_core(value), _name_core(target)
    if not core_v or not core_t:
        return False
    return core_v in core_t or core_t in core_v


def _amount_anchors(biz: LedgerBusiness) -> list[tuple[Decimal, str, str]]:
    """台账里可作为归属锚点的金额，按方向归集。"""
    anchors: list[tuple[Decimal, str, str]] = []

    def add(value, direction: str, label: str) -> None:
        if value is None or value <= _ZERO:
            return
        if any(abs(value - a[0]) <= _AMOUNT_TOL for a in anchors):
            return
        anchors.append((value, direction, label))

    add(biz.up_contract_amount, DIRECTION_UP, "采购合同金额")
    add(biz.up_settlement, DIRECTION_UP, "采购结算金额")
    add(biz.pay_amount, DIRECTION_UP, "付款金额")
    add(biz.down_contract_amount, DIRECTION_DOWN, "销售合同金额")
    add(biz.down_settlement, DIRECTION_DOWN, "销售结算金额")
    add(biz.receipt_amount, DIRECTION_DOWN, "回款金额")
    add(biz.interest_received, DIRECTION_DOWN, "实收利息")
    if biz.receipt_amount is not None and biz.interest_received is not None:
        add(biz.receipt_amount + biz.interest_received, DIRECTION_DOWN, "回款含实收利息")
    if biz.down_settlement is not None and biz.interest_received is not None:
        add(biz.down_settlement + biz.interest_received, DIRECTION_DOWN, "销售结算含利息")
    return anchors


def _assign_single(doc: DocRecord, biz: LedgerBusiness) -> tuple[str, str]:
    """单份文档对单条业务线的归属判定：金额口径（强）优先，其次对手方公司名。"""
    if doc.amount is not None:
        for value, direction, label in _amount_anchors(biz):
            if abs(doc.amount - value) <= _AMOUNT_TOL:
                return direction, f"amount:{label}"
    if biz.supplier and _name_match(doc.seller, biz.supplier):
        return DIRECTION_UP, "party:seller"
    if biz.customer and _name_match(doc.company, biz.customer):
        return DIRECTION_DOWN, "party:buyer"
    if biz.supplier and _name_match(doc.company, biz.supplier):
        return DIRECTION_UP, "party:buyer"
    if biz.customer and _name_match(doc.seller, biz.customer):
        return DIRECTION_DOWN, "party:seller"
    return "", ""


# ---------- 货权单 / 放货通知的上下游判定 ----------
#
# 业务口径（用户确认）：放货给云创/陕建的是上游货权（供应商把货权放给我们），
# 其余（提货权转给下游钢厂等终端客户）是下游货权。
# 这类单据既没有合同号也没有金额，归属只能靠正文里的船名对台账，
# 方向则读「货权转移给谁」的接收方 —— 两者缺一不硬判。

_OUR_ENTITY_KEYWORDS = ("云创", "陕建")

_CARGO_NAME_SUFFIX = r"(?:有限公司|有限责任公司|股份有限公司)"
_CARGO_RECEIVER_PATTERNS = (
    # 「公司名称：山西宏达钢铁有限公司」
    re.compile(rf"公司名称[:：]\s*([^，。；\n]{{2,40}}?{_CARGO_NAME_SUFFIX})"),
    # 「货权转移给“华山云创供应链科技(山西)有限公司”。」
    re.compile(rf"转移给[下列]*[“\"'《]?([^”\"'《，。；\n]{{2,40}}?{_CARGO_NAME_SUFFIX})"),
)


def _cargo_receiver(text: str) -> str:
    """从货权单正文抽「货权转移给谁」的接收方，抽不到返空串。"""
    if not text:
        return ""
    for pattern in _CARGO_RECEIVER_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return ""


def _is_our_entity(name: str) -> bool:
    core = _name_core(name)
    return bool(core) and any(k in core for k in _OUR_ENTITY_KEYWORDS)


def _cargo_direction(doc: DocRecord, biz: LedgerBusiness) -> tuple[str, str]:
    """货权单方向：接收方是我方主体 → 上游；转给下游客户 → 下游。"""
    text = norm_text(doc.text)
    receiver = _cargo_receiver(text)
    if receiver:
        if _is_our_entity(receiver):
            return DIRECTION_UP, "cargo:receiver_self"
        if biz.customer and _name_match(receiver, biz.customer):
            return DIRECTION_DOWN, "cargo:receiver_customer"
        if biz.supplier and _name_match(receiver, biz.supplier):
            return DIRECTION_UP, "cargo:receiver_supplier"
    # 正文被截断时接收方可能读不到，但抄送（CC）行里仍留着我方主体
    if any(k in text for k in _OUR_ENTITY_KEYWORDS):
        return DIRECTION_UP, "cargo:self_in_text"
    return "", ""


def _cargo_assign(doc: DocRecord, biz: LedgerBusiness) -> tuple[str, str]:
    """货权单一步到位：船名认业务线，接收方认方向。"""
    if doc.role != ROLE_CARGO_RIGHT:
        return "", ""
    text = norm_text(doc.text)
    if not text:
        return "", ""
    if not biz.vessel or norm_text(biz.vessel) not in text:
        return "", ""
    direction, basis = _cargo_direction(doc, biz)
    if not direction:
        return "", ""
    return direction, f"{basis}+vessel"


def assign_documents(docs: list[DocRecord], biz: LedgerBusiness) -> None:
    """就地给「方向未知但角色明确」的文档补归属，命中即写 direction/assigned_by。"""
    pending = [d for d in docs if d.direction == DIRECTION_UNKNOWN and d.role != "other"]
    if not pending:
        return

    # 0) 货权单：正文接收方 + 船名，优先级高于金额与公司名
    for d in pending:
        if d.role == ROLE_CARGO_RIGHT:
            direction, basis = _cargo_assign(d, biz)
            if direction:
                d.direction, d.assigned_by = direction, basis

    # 1) 金额口径 / 对手方公司名
    for d in pending:
        direction, basis = _assign_single(d, biz)
        if direction:
            d.direction, d.assigned_by = direction, basis

    anchors = _amount_anchors(biz)

    # 2) 单份金额命中台账口径
    for d in pending:
        if d.direction != DIRECTION_UNKNOWN or d.amount is None:
            continue
        for value, direction, label in anchors:
            if abs(d.amount - value) <= _AMOUNT_TOL:
                d.direction, d.assigned_by = direction, f"amount:{label}"
                break

    # 3) 同角色多份合计命中台账口径（分笔付款/回款）：子集求和，张数少时枚举
    for role in {d.role for d in pending}:
        group = [
            d for d in pending
            if d.role == role and d.direction == DIRECTION_UNKNOWN and d.amount is not None
        ]
        if not 2 <= len(group) <= 8:
            continue
        best: tuple[list[DocRecord], str, str] | None = None
        for mask in range(1, 1 << len(group)):
            subset = [group[i] for i in range(len(group)) if mask >> i & 1]
            total = sum((x.amount for x in subset), _ZERO)
            for value, direction, label in anchors:
                if abs(total - value) <= _AMOUNT_TOL:
                    if best is None or len(subset) > len(best[0]):
                        best = (subset, direction, label)
        if best is not None:
            for d in best[0]:
                d.direction, d.assigned_by = best[1], f"amount_sum:{best[2]}"


# ==================== 规则引擎 ====================

def _sum_amount(docs: list[DocRecord]) -> Optional[Decimal]:
    if not docs:
        return None
    values = [d.amount for d in docs if d.amount is not None]
    if not values:
        return None
    return sum(values, _ZERO)


def _sum_goods(docs: list[DocRecord]) -> Optional[Decimal]:
    """货款性质合计：保证金、退款不并入回款/付款总额。

    保证金是独立科目，退款是对已付款的冲减，两者并进货款合计都会让
    「回单合计 ↔ 台账金额」这一步凭空多出或少掉一笔。
    """
    return _sum_amount([d for d in docs if d.nature == NATURE_PAYMENT])


def _diff_amount(title: str, expected: Optional[Decimal], actual: Optional[Decimal],
                 direction: str, role: str, docs: list[int], hint: str = "") -> Optional[DiffItem]:
    if expected is None or actual is None:
        return None
    delta = actual - expected
    if abs(delta) <= RECON_TOLERANCE:
        return None
    return DiffItem(
        type=DIFF_AMOUNT_MISMATCH,
        severity=SEVERITY_CRITICAL,
        title=title,
        direction=direction,
        role=role,
        expected=fmt_amount(expected),
        actual=fmt_amount(actual),
        delta=fmt_amount(delta),
        message=hint or f"差额 {fmt_amount(delta)} 元",
        document_ids=docs,
    )


def check_business(biz: LedgerBusiness, docs: list[DocRecord],
                   cross_dup_ids: set[int] | None = None,
                   statement_docs: list[DocRecord] | None = None) -> tuple[list[DiffItem], list[MatrixCell], list[int]]:
    """跑 R1–R7，返回（差异, 矩阵, 未归属文档 id）。"""
    diffs: list[DiffItem] = []
    matrix: list[MatrixCell] = []
    unlinked: list[int] = []
    cross_dup = cross_dup_ids or set()

    # 无合同号的单据（发票/回单/货权单）先按对手方公司名、金额锚点补归属
    assign_documents(docs, biz)

    by_cell: dict[tuple[str, str], list[DocRecord]] = {}
    for d in docs:
        if d.direction == DIRECTION_UNKNOWN:
            if d.role != "other":
                unlinked.append(d.document_id)
            continue
        by_cell.setdefault((d.direction, d.role), []).append(d)

    # 同一张单据被重复上传（同一份结算单存了两个副本、同一张图改了文件名）时，
    # 金额会被算两遍：下游结算 7,922,962.05 × 2 = 1,584.59 万，直接变成
    # 「与台账 792 万不符」。两道判据：①图像指纹（同一张图，抽取字段抽错也认）；
    # ②业务键「同合同号 + 同金额 + 同日期」。命中任一即视为同一张单据，
    # 金额只计一份；张数照实显示，并把重复项单独提示。
    by_cell_amt: dict[tuple[str, str], list[DocRecord]] = {}
    dup_groups: list[tuple[DocRecord, DocRecord, str]] = []
    for key, cell_docs in by_cell.items():
        seen: dict[tuple, DocRecord] = {}
        uniq: list[DocRecord] = []
        for d in cell_docs:
            # 第一层：图像指纹判重。业务键要求「合同号 + 金额 + 日期」三样都抽对，
            # 任一项抽错就失效；同一张图重拍/重存时 dHash 仍然相等，兜住这个缺口。
            fp_keep = next((k for k in uniq if _same_image_record(k, d)), None)
            if fp_keep is not None:
                dup_groups.append((fp_keep, d, "image"))
                continue
            dkey = (d.contract_no, d.amount, d.doc_date)
            identifiable = d.amount is not None and bool(d.contract_no) and d.doc_date is not None
            if identifiable and dkey in seen:
                dup_groups.append((seen[dkey], d, "business_key"))
                continue
            if identifiable:
                seen[dkey] = d
            uniq.append(d)
        by_cell_amt[key] = uniq

    contract_nos = _variants_of(_all_contract_nos(biz))

    # 重复单据提示（金额已按一份计，这里只做说明）
    for keep, dup, reason in dup_groups:
        if reason == "image":
            basis = (f"两张图的感知哈希（dHash）一致且像素尺寸相同，"
                     f"判定为同一张单据的两个副本（{keep.filename} / {dup.filename}）")
        else:
            basis = (f"合同号 {keep.contract_no} / 金额 {fmt_amount(keep.amount or _ZERO)} / "
                     f"日期 {keep.doc_date} 完全一致，判定为同一张单据的两个副本")
        diffs.append(DiffItem(
            type=DIFF_AMOUNT_MISMATCH,
            severity=SEVERITY_INFO,
            title="同一张单据重复上传（金额按一份计）",
            direction=keep.direction, role=keep.role,
            expected=f"{keep.filename}",
            actual=f"{dup.filename}",
            message=f"{basis}，汇总金额只计一份，避免翻倍",
            document_ids=[keep.document_id, dup.document_id],
        ))

    # ---- 业财对账单（一份文档合并多条业务线）----
    # 这类单据归为 other 后不会进矩阵，这里显式说明它属于本业务线，
    # 免得用户以为"这份对账单没被系统认出来"。
    for d in (statement_docs or []):
        if d.role != "other":
            continue
        if "对账" not in f"{d.document_type}{d.filename}":
            continue
        if "结算单" in f"{d.document_type}{d.filename}":
            continue
        hit = _statement_mentions(d.text, contract_nos)
        if not hit:
            continue
        diffs.append(DiffItem(
            type=DIFF_DUPLICATE_BIZ,
            severity=SEVERITY_INFO,
            title="业财对账单已按合同号归属（多业务线合并单据）",
            role=d.role,
            expected=hit[0],
            actual=d.filename,
            message=(f"该对账单正文同时列了多条业务线的合同编号，本业务线命中 {hit[0]}；"
                     "对账单是汇总凭证，其金额已由结算单/发票/回单分别核对，"
                     "这里不重复计入矩阵金额"),
            document_ids=[d.document_id],
        ))

    # ---- R1 单据齐套（2×5，缺件不豁免）----
    for direction in (DIRECTION_UP, DIRECTION_DOWN):
        for role in RECON_ROLES:
            cell_docs = by_cell.get((direction, role), [])
            cell_docs_amt = by_cell_amt.get((direction, role), [])
            candidates = [
                d.document_id for d in docs
                if d.role == role and d.direction == DIRECTION_UNKNOWN
            ]
            amount = _sum_amount(cell_docs_amt)
            goods_amount = (
                _sum_goods(cell_docs_amt) if role == ROLE_PAYMENT_RECEIPT else amount
            )
            matrix.append(MatrixCell(
                direction=direction, role=role,
                count=len(cell_docs), amount=float(goods_amount or 0),
                document_ids=[d.document_id for d in cell_docs],
                candidate_count=len(candidates),
            ))
            extras = [d for d in cell_docs if d.nature != NATURE_PAYMENT]
            if extras:
                labels = "、".join(
                    f"{'保证金' if d.nature == NATURE_DEPOSIT else '退款'} "
                    f"{fmt_amount(d.amount or _ZERO)}"
                    for d in extras
                )
                diffs.append(DiffItem(
                    type=DIFF_AMOUNT_MISMATCH,
                    severity=SEVERITY_INFO,
                    title=f"{_dir_label(direction)}{ROLE_LABELS[role]}含非货款款项（单独核算）",
                    direction=direction, role=role,
                    expected="仅货款参与金额勾稽",
                    actual=labels,
                    message="保证金/退款不属于货款，已从回款合计中剔除，不在金额差异中体现",
                    document_ids=[d.document_id for d in extras],
                ))
            if cell_docs:
                # 有单据但金额识别失败
                if role in (ROLE_SETTLEMENT, ROLE_INVOICE, ROLE_PAYMENT_RECEIPT) and amount is None:
                    diffs.append(DiffItem(
                        type=DIFF_UNPARSED_AMOUNT,
                        severity=SEVERITY_WARNING,
                        title=f"{_dir_label(direction)}{ROLE_LABELS[role]}金额未识别",
                        direction=direction, role=role,
                        message="文档已归档但金额字段为空，无法参与金额勾稽，请补录",
                        document_ids=[d.document_id for d in cell_docs],
                    ))
                continue
            diffs.append(DiffItem(
                type=DIFF_MISSING_DOC,
                # 库里有同角色的单据但方向判不出来（货权单只有船名/吨位、
                # 转出方与接收方都不是本业务线主体时无法二分上下游），
                # 这不是"缺件"，是"方向待确认"，不该按 CRITICAL 报。
                severity=SEVERITY_WARNING if candidates else SEVERITY_CRITICAL,
                title=f"缺{_dir_label(direction)}{ROLE_LABELS[role]}",
                direction=direction, role=role,
                expected="应有 1 份及以上",
                actual="0 份",
                message=(
                    f"库中有 {len(candidates)} 份疑似{ROLE_LABELS[role]}，"
                    "但缺少转出方/接收方信息无法判定上下游，请人工确认方向"
                    if candidates else "库中未找到该业务线对应单据"
                ),
                candidate_document_ids=candidates,
            ))

    # ---- R2 上游三单一致：结算 ↔ 发票 ↔ 付款 ---- 
    up_settle = biz.up_settlement
    up_invoice = _sum_amount(by_cell_amt.get((DIRECTION_UP, ROLE_INVOICE), []))
    up_pay = biz.pay_amount
    up_inv_docs = [d.document_id for d in by_cell.get((DIRECTION_UP, ROLE_INVOICE), [])]
    if up_settle is not None and up_invoice is not None:
        item = _diff_amount("上游发票金额与采购结算金额不符", up_settle, up_invoice,
                            DIRECTION_UP, ROLE_INVOICE, up_inv_docs)
        if item:
            diffs.append(item)
    if up_settle is not None and up_pay is not None:
        # 台账内部：付款额与结算额的差额属正常业务（预付/尾款/尾数），仅提示不判错
        delta = up_pay - up_settle
        if abs(delta) > RECON_TOLERANCE:
            diffs.append(DiffItem(
                type=DIFF_AMOUNT_MISMATCH, severity=SEVERITY_INFO,
                title="上游付款金额与采购结算金额存在差额（预付/尾款）",
                direction=DIRECTION_UP, role=ROLE_PAYMENT_RECEIPT,
                expected=fmt_amount(up_settle), actual=fmt_amount(up_pay),
                delta=fmt_amount(delta),
                message=f"台账付款 {fmt_amount(up_pay)} / 采购结算 {fmt_amount(up_settle)}，差额 {fmt_amount(delta)}",
            ))

    # 上游付款回单（凭证）↔ 台账支付金额：两者应一致；少于此额为部分付款，多则为超额
    up_pay_docs = by_cell.get((DIRECTION_UP, ROLE_PAYMENT_RECEIPT), [])
    up_pay_doc_sum = _sum_goods(by_cell_amt.get((DIRECTION_UP, ROLE_PAYMENT_RECEIPT), []))
    if up_pay is not None and up_pay_doc_sum is not None:
        delta = up_pay_doc_sum - up_pay
        if abs(delta) > RECON_TOLERANCE:
            diffs.append(DiffItem(
                type=DIFF_AMOUNT_MISMATCH,
                severity=SEVERITY_CRITICAL if delta > 0 else SEVERITY_WARNING,
                title="上游付款回单合计与台账支付金额不符",
                direction=DIRECTION_UP, role=ROLE_PAYMENT_RECEIPT,
                expected=fmt_amount(up_pay), actual=fmt_amount(up_pay_doc_sum),
                delta=fmt_amount(delta),
                message="回单合计超过台账付款额" if delta > 0 else "回单未覆盖全部付款，可能分次付款",
                document_ids=[d.document_id for d in up_pay_docs],
            ))

    # ---- R3 下游三单一致：结算 ↔ 发票 ↔ 回款 ---- 
    down_settle = biz.down_settlement
    down_invoice = _sum_amount(by_cell_amt.get((DIRECTION_DOWN, ROLE_INVOICE), []))
    down_receipt = biz.receipt_amount
    down_inv_docs = [d.document_id for d in by_cell.get((DIRECTION_DOWN, ROLE_INVOICE), [])]
    if down_settle is not None and down_invoice is not None:
        item = _diff_amount("下游发票金额与销售结算金额不符", down_settle, down_invoice,
                            DIRECTION_DOWN, ROLE_INVOICE, down_inv_docs)
        if item:
            diffs.append(item)
    if down_settle is not None and down_receipt is not None:
        # 台账内部：已回款额与结算额的差额=尚未回款，仅提示不判错
        delta = down_receipt - down_settle
        if abs(delta) > RECON_TOLERANCE:
            diffs.append(DiffItem(
                type=DIFF_AMOUNT_MISMATCH, severity=SEVERITY_INFO,
                title="下游回款金额与销售结算金额存在差额（尚未回全）",
                direction=DIRECTION_DOWN, role=ROLE_PAYMENT_RECEIPT,
                expected=fmt_amount(down_settle), actual=fmt_amount(down_receipt),
                delta=fmt_amount(delta),
                message=f"台账回款 {fmt_amount(down_receipt)} / 销售结算 {fmt_amount(down_settle)}，差额 {fmt_amount(delta)}",
            ))

    # 下游收款回单（凭证）↔ 台账回款金额 + 实收利息
    #
    # 台账「回款额」登记的是本金，客户实付时连带利息一起打款，
    # 所以回单合计应与「回款 + 实收利息」比，而不是只比本金
    # （6,757,500 + 40,545 = 6,798,045 正好等于三张回款回单）。
    down_receipt_docs = by_cell.get((DIRECTION_DOWN, ROLE_PAYMENT_RECEIPT), [])
    down_receipt_sum = _sum_goods(by_cell_amt.get((DIRECTION_DOWN, ROLE_PAYMENT_RECEIPT), []))
    expected_receipt = down_receipt
    if expected_receipt is not None and biz.interest_received is not None:
        expected_receipt = expected_receipt + biz.interest_received
    if expected_receipt is not None and down_receipt_sum is not None:
        delta = down_receipt_sum - expected_receipt
        if abs(delta) > RECON_TOLERANCE:
            diffs.append(DiffItem(
                type=DIFF_AMOUNT_MISMATCH,
                severity=SEVERITY_CRITICAL if delta > 0 else SEVERITY_WARNING,
                title="下游收款回单合计与台账回款金额不符",
                direction=DIRECTION_DOWN, role=ROLE_PAYMENT_RECEIPT,
                expected=fmt_amount(expected_receipt), actual=fmt_amount(down_receipt_sum),
                delta=fmt_amount(delta),
                message="回单合计超过台账回款额（含利息）" if delta > 0 else "回单未覆盖全部回款，可能分次回款",
                document_ids=[d.document_id for d in down_receipt_docs],
            ))

    # ---- R4 跨端利息：下游结算 − 上游结算 = 应收利息 ----
    # 台账实际存在"应收已登记、尚未收全"的业务线：差额可能等于 R（应收）
    # 也可能等于 S（实收）。因此 R / S 任一命中即视为勾稽通过，
    # 只有两者都不等才判定利息不符；R ≠ S 时给一条 INFO 提示。
    if up_settle is not None and down_settle is not None:
        gap = down_settle - up_settle
        r_int = biz.interest_receivable
        s_int = biz.interest_received
        hit_r = r_int is not None and abs(gap - r_int) <= RECON_TOLERANCE
        hit_s = s_int is not None and abs(gap - s_int) <= RECON_TOLERANCE
        if hit_r or hit_s:
            if r_int is not None and s_int is not None and abs(r_int - s_int) > RECON_TOLERANCE:
                diffs.append(DiffItem(
                    type=DIFF_INTEREST_MISMATCH,
                    severity=SEVERITY_INFO,
                    title="应收利息与实收利息存在差额（利息尚未收全）",
                    direction="cross", role="interest",
                    expected=fmt_amount(r_int),
                    actual=fmt_amount(s_int),
                    delta=fmt_amount(s_int - r_int),
                    message=f"上下游结算差额 {fmt_amount(gap)}，"
                            f"{'按应收' if hit_r else '按实收'}口径勾稽一致",
                ))
        elif r_int is not None or s_int is not None:
            reference = r_int if r_int is not None else s_int
            diffs.append(DiffItem(
                type=DIFF_INTEREST_MISMATCH,
                severity=SEVERITY_CRITICAL,
                title="应收利息与上下游结算差额不符",
                direction="cross", role="interest",
                expected=fmt_amount(gap),
                actual=fmt_amount(reference),
                delta=fmt_amount(reference - gap),
                message=f"下游结算 {fmt_amount(down_settle)} − 上游结算 {fmt_amount(up_settle)} = "
                        f"{fmt_amount(gap)}，台账应收 {fmt_amount(r_int)} / 实收 {fmt_amount(s_int)}",
            ))

    # ---- R5 合同归属 ---- 
    for d in docs:
        if d.direction == DIRECTION_UNKNOWN or not d.contract_no:
            continue
        variants = contract_variants(d.contract_no)
        if variants & contract_nos:
            continue
        # 合同正文里自写的编号（如 HSYC-TZS-20260512，TZS = 船名简称）与台账
        # 登记的两套编号（HSYCXS（DD）-… / SOTC…）本就不是一个体系，若单据
        # 金额又能与台账某一锚点完全对上，说明就是本业务线的单据，只作提示。
        anchored = any(
            abs((d.amount or _ZERO) - a[0]) <= RECON_TOLERANCE
            for a in _amount_anchors(biz)
        ) if d.amount is not None else False
        diffs.append(DiffItem(
            type=DIFF_CHAIN_BREAK,
            severity=SEVERITY_INFO if anchored else SEVERITY_WARNING,
            title=f"单据合同号不属于本业务线：{d.contract_no}",
            direction=d.direction, role=d.role,
            expected=f"{biz.up_contract_no} / {biz.down_contract_no}",
            actual=d.contract_no,
            message=(
                f"该编号为合同正文自写编号，与台账登记编号体系不同；"
                f"单据金额 {fmt_amount(d.amount or _ZERO)} 与台账口径一致，判定为本业务线单据"
                if anchored else ""
            ),
            document_ids=[d.document_id],
        ))

    # ---- R6 日期时序 ---- 
    for d in docs:
        if not d.doc_date:
            continue
        if d.direction == DIRECTION_UP and biz.up_contract_date and d.doc_date < biz.up_contract_date:
            diffs.append(DiffItem(
                type=DIFF_DATE_CONFLICT,
                severity=SEVERITY_WARNING,
                title=f"{ROLE_LABELS.get(d.role, d.role)}日期早于采购合同签订日期",
                direction=DIRECTION_UP, role=d.role,
                expected=f"≥ {biz.up_contract_date.isoformat()}",
                actual=d.doc_date.isoformat(),
                document_ids=[d.document_id],
            ))
        if d.direction == DIRECTION_DOWN and biz.down_contract_date and d.doc_date < biz.down_contract_date:
            diffs.append(DiffItem(
                type=DIFF_DATE_CONFLICT,
                severity=SEVERITY_WARNING,
                title=f"{ROLE_LABELS.get(d.role, d.role)}日期早于销售合同签订日期",
                direction=DIRECTION_DOWN, role=d.role,
                expected=f"≥ {biz.down_contract_date.isoformat()}",
                actual=d.doc_date.isoformat(),
                document_ids=[d.document_id],
            ))

    # ---- R7 编号唯一 ---- 
    seen_invoice: dict[str, int] = {}
    for d in docs:
        if d.role != ROLE_INVOICE or not d.invoice_no:
            continue
        if d.invoice_no in seen_invoice:
            diffs.append(DiffItem(
                type=DIFF_DUPLICATE_BIZ,
                severity=SEVERITY_WARNING,
                title=f"发票号重复：{d.invoice_no}",
                direction=d.direction, role=ROLE_INVOICE,
                actual=d.invoice_no,
                document_ids=[seen_invoice[d.invoice_no], d.document_id],
            ))
        else:
            seen_invoice[d.invoice_no] = d.document_id

    for d in docs:
        if d.document_id in cross_dup:
            diffs.append(DiffItem(
                type=DIFF_DUPLICATE_BIZ,
                severity=SEVERITY_WARNING,
                title="同一文档关联到多条业务线",
                role=d.role, direction=d.direction,
                message=f"文档 {d.filename} 同时命中其他业务线，请人工确认归属",
                document_ids=[d.document_id],
            ))

    # ---- 台账缺行 / 未分类文档 ---- 
    if biz.settlement_missing:
        diffs.append(DiffItem(
            type=DIFF_LEDGER_MISSING,
            severity=SEVERITY_WARNING,
            title="结算数据 sheet 未登记该业务线",
            message=f"按销售合同号 {biz.down_contract_no} 未找到结算行，金额勾稽已跳过",
        ))
    if biz.payment_missing:
        diffs.append(DiffItem(
            type=DIFF_LEDGER_MISSING,
            severity=SEVERITY_WARNING,
            title="宏达收付款台账未登记该业务线",
            message=f"按销售合同编号 {biz.down_contract_no} 未找到收付款行，利息/回款校验已跳过",
        ))
    if unlinked:
        diffs.append(DiffItem(
            type=DIFF_UNCLASSIFIED_DOC,
            severity=SEVERITY_INFO,
            title="有单据无法判定上下游方向",
            message="合同号缺失或前缀不在已知编号体系中，未纳入矩阵统计",
            document_ids=list(unlinked),
        ))

    return diffs, matrix, unlinked


@dataclass
class MatrixCell:
    direction: str
    role: str
    count: int = 0
    amount: float = 0.0
    document_ids: list[int] = field(default_factory=list)
    candidate_count: int = 0


def _dir_label(direction: str) -> str:
    return {DIRECTION_UP: "上游", DIRECTION_DOWN: "下游"}.get(direction, "")


# ==================== 对外入口 ====================

class ReconciliationService:
    """对账服务。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- 台账源定位 ----
    def resolve_source(self, source_id: Optional[int] = None) -> ExcelSource:
        if source_id:
            src = self.db.execute(
                select(ExcelSource).where(ExcelSource.id == source_id)
            ).scalar_one_or_none()
            if src is None:
                raise ReconciliationError(f"台账源不存在：{source_id}")
            return src
        src = self.db.execute(
            select(ExcelSource).where(ExcelSource.enabled.is_(True)).order_by(ExcelSource.id)
        ).scalars().first()
        if src is None:
            raise ReconciliationError("尚未登记台账源，请先在「台账源」页上传台账 xlsx")
        return src

    # ---- 文档归位 ----
    def _bind_documents(self, businesses: list[LedgerBusiness],
                        docs: list[DocRecord]) -> tuple[dict[str, list[DocRecord]], set[int],
                                                         list[DocRecord], dict[str, list[DocRecord]]]:
        index: dict[str, set[str]] = {}
        up_variants: dict[str, set[str]] = {}
        down_variants: dict[str, set[str]] = {}
        for biz in businesses:
            up_variants[biz.business_no] = _variants_of(
                biz.up_contract_nos or [biz.up_contract_no, biz.up_alt_no]
            )
            down_variants[biz.business_no] = _variants_of(
                biz.down_contract_nos or [biz.down_contract_no, biz.down_alt_no]
            )
            for variant in _variants_of(_all_contract_nos(biz)):
                index.setdefault(variant, set()).add(biz.business_no)

        bound: dict[str, list[DocRecord]] = {b.business_no: [] for b in businesses}
        # 归不了业务线的单据不能直接丢掉：货权单这类既没有合同号、也没有
        # 金额和对手方主体的单据，只有"库里有 4 份、但判不出上下游"这条信息
        # 本身对用户有用（提示人工确认方向）。统一收进 pool 交给 check_business。
        pool: list[DocRecord] = []
        # 业财对账单：一份文档合并多条业务线的对账汇总，按正文合同号认领。
        # 它绝不能塞进 bound —— bound 非空会让该业务线从 no_data 变成"有资料"，
        # 进而把公共 pool 广播进来，把别的业务线的发票/回单按金额锚点抢过来。
        # 单独用 statements 传下去，只做「已归属」说明，不参与任何金额统计。
        statements: dict[str, list[DocRecord]] = {}
        dup_ids: set[int] = set()
        for doc in docs:
            doc_variants = contract_variants(doc.contract_no)
            hits: set[str] = set()
            for variant in doc_variants:
                hits |= index.get(variant, set())

            if hits:
                if len(hits) > 1:
                    dup_ids.add(doc.document_id)
                for business_no in hits:
                    linked = DocRecord(**{**doc.__dict__})
                    # 方向：合同号落在上游集合还是下游集合
                    if doc_variants & up_variants.get(business_no, set()):
                        linked.direction = DIRECTION_UP
                    elif doc_variants & down_variants.get(business_no, set()):
                        linked.direction = DIRECTION_DOWN
                    if linked.file_role in ("contract_up", "contract_down"):
                        linked.direction = infer_direction(linked.file_role, linked.contract_no)
                    bound[business_no].append(linked)
                continue

            # 合同号为空、或合同号不属于任何业务线：按金额口径 / 对手方公司名推断。
            # 只有唯一命中一条业务线时才认，避免同客户的多个业务线互相抢文档。
            if doc.role == "other":
                # 业财对账单这类汇总单据：正文列了多条业务线的合同号，按正文编号
                # 认领到对应业务线（可能同时属于多条）。认领结果单独记到
                # statements，不进 bound / 不进 pool —— 见上方注释。
                claimed: set[str] = set()
                for b in businesses:
                    if _statement_mentions(doc.text, _variants_of(_all_contract_nos(b))):
                        claimed.add(b.business_no)
                for business_no in claimed:
                    statements.setdefault(business_no, []).append(doc)
                if not claimed:
                    pool.append(doc)
                continue
            if doc.role == ROLE_CARGO_RIGHT:
                # 货权单没有合同号和金额，按「正文船名 + 接收方」判归属与方向
                candidates = [(b.business_no, *_cargo_assign(doc, b)) for b in businesses]
            else:
                candidates = [(b.business_no, *_assign_single(doc, b)) for b in businesses]
            hits = [c for c in candidates if c[1]]
            if len(hits) != 1:
                pool.append(doc)
                continue
            business_no, direction, basis = hits[0]
            linked = DocRecord(**{**doc.__dict__})
            linked.direction = direction
            linked.assigned_by = basis
            bound[business_no].append(linked)
        return bound, dup_ids, pool, statements

    # ---- 主流程 ----
    def run(self, business_no: Optional[str] = None, source_id: Optional[int] = None) -> dict:
        started = time.time()
        src = self.resolve_source(source_id)
        reader = LedgerReader(src.file_path)
        try:
            businesses = reader.load()
        finally:
            reader.close()
        if not businesses:
            raise ReconciliationError("台账里没有读到有效业务线（检查「业务线」列是否有值）")

        if business_no:
            target = norm_no(business_no)
            businesses = [b for b in businesses if b.business_no == target]
            if not businesses:
                raise ReconciliationError(f"台账中不存在业务线：{business_no}")

        docs = _load_documents(self.db)
        bound, dup_ids, pool, statements = self._bind_documents(businesses, docs)

        ok = warning = error = no_data = 0
        unlinked_total = 0
        unlinked_ids: set[int] = set()
        sheets: list[str] = []
        now = datetime.now()

        for biz in businesses:
            # pool 只用来提供"库里有疑似XX但方向判不出来"的候选与未归属清单，
            # 其 direction 全是 unknown，不会影响本业务线的金额勾稽。
            # 但 pool 绝不能广播给所有业务线：否则每条没资料的业务线都会因为
            # "共享的未归属单据"被算成"有资料"，no_data 判定失效。只给本身
            # 已经绑定了单据的业务线做候选提示。
            own = bound.get(biz.business_no, [])
            docs_for_biz = own + pool if own else []
            diffs, matrix, unlinked = check_business(
                biz, docs_for_biz, dup_ids, statements.get(biz.business_no, [])
            )
            unlinked_ids.update(unlinked)
            if biz.ledger_sheet not in sheets:
                sheets.append(biz.ledger_sheet)

            # 这条业务线一份单据都没关联上：2×5 全空不是"缺件"，是"还没上传资料"。
            # 此时不再把 10 格缺失报成 CRITICAL，避免几十条没资料的业务线全红。
            has_docs = any(m.count > 0 for m in matrix)
            active = [d for d in diffs if not d.ignored]
            severities = [d.severity for d in active]
            if not has_docs:
                status = RECON_STATUS_NO_DATA
                no_data += 1
                # 一份单据都没有时，"2×5 全空"推出来的 10 条缺件与真差异不是
                # 一回事，留着只会把真差异埋掉；结论由 note 说明，矩阵仍给出。
                diffs = []
                active = []
            elif SEVERITY_CRITICAL in severities:
                status = RECON_STATUS_ERROR
                error += 1
            elif SEVERITY_WARNING in severities:
                status = RECON_STATUS_WARNING
                warning += 1
            else:
                # 只剩 INFO：金额勾稽全部成立，INFO 只是"已核对、无需处理"的说明
                status = RECON_STATUS_OK
                ok += 1

            payload = {
                "business_no": biz.business_no,
                "ledger_sheet": biz.ledger_sheet,
                "vessel": biz.vessel,
                "material": biz.material,
                "supplier": biz.supplier,
                "customer": biz.customer,
                "up_contract_no": biz.up_contract_no,
                "down_contract_no": biz.down_contract_no,
                "note": ("" if has_docs else
                         "该业务线尚未上传任何单据资料，无法对账。"
                         "上传资料并重跑识别后再次对账即可。"),
                "up_contract_amount": fmt_amount(biz.up_contract_amount),
                "down_contract_amount": fmt_amount(biz.down_contract_amount),
                "up_settlement": fmt_amount(biz.up_settlement),
                "down_settlement": fmt_amount(biz.down_settlement),
                "interest_receivable": fmt_amount(biz.interest_receivable),
                "interest_received": fmt_amount(biz.interest_received),
                "pay_amount": fmt_amount(biz.pay_amount),
                "receipt_amount": fmt_amount(biz.receipt_amount),
                "matrix": [m.__dict__ for m in matrix],
                "diffs": [d.to_dict() for d in diffs],
                "unlinked_document_ids": unlinked,
            }

            self._upsert(
                business_no=biz.business_no,
                ledger_sheet=biz.ledger_sheet,
                up_contract_no=biz.up_contract_no,
                down_contract_no=biz.down_contract_no,
                up_settlement=biz.up_settlement or _ZERO,
                down_settlement=biz.down_settlement or _ZERO,
                interest_receivable=biz.interest_receivable or _ZERO,
                interest_received=biz.interest_received or _ZERO,
                status=status,
                diff_count=len(active),
                payload=payload,
                checked_at=now,
            )

        self.db.commit()
        unlinked_total = len(unlinked_ids)
        duration = int((time.time() - started) * 1000)
        logger.info("对账完成：%d 条业务线，%d 处差异，用时 %dms",
                    len(businesses), error + warning, duration)
        return {
            "total": len(businesses),
            "ok": ok,
            "warning": warning,
            "error": error,
            "no_data": no_data,
            "unlinked": unlinked_total,
            "ledger_sheets": sheets,
            "duration_ms": duration,
        }

    def _upsert(self, *, business_no: str, ledger_sheet: str, up_contract_no: str,
                down_contract_no: str, up_settlement: Decimal, down_settlement: Decimal,
                interest_receivable: Decimal, interest_received: Decimal,
                status: str, diff_count: int, payload: dict, checked_at: datetime) -> None:
        row = self.db.execute(
            select(ReconciliationReport).where(ReconciliationReport.business_no == business_no)
        ).scalar_one_or_none()
        report_json = json.dumps(payload, ensure_ascii=False)
        if row is None:
            row = ReconciliationReport(business_no=business_no)
            self.db.add(row)
        row.ledger_sheet = ledger_sheet
        row.up_contract_no = up_contract_no
        row.down_contract_no = down_contract_no
        row.up_settlement = up_settlement
        row.down_settlement = down_settlement
        row.interest_receivable = interest_receivable
        row.interest_received = interest_received
        row.status = status
        row.diff_count = diff_count
        row.report_json = report_json
        row.checked_at = checked_at

    # ---- 未归属文档 ----
    def list_unlinked(self, source_id: Optional[int] = None) -> list[dict]:
        src = self.resolve_source(source_id)
        reader = LedgerReader(src.file_path)
        try:
            businesses = reader.load()
        finally:
            reader.close()
        index: dict[str, set[str]] = {}
        for biz in businesses:
            for variant in _variants_of(_all_contract_nos(biz)):
                index.setdefault(variant, set()).add(biz.business_no)

        out: list[dict] = []
        for doc in _load_documents(self.db):
            hits = set()
            for variant in contract_variants(doc.contract_no):
                hits |= index.get(variant, set())
            if hits:
                continue
            reason = "无合同号" if not doc.contract_no else "合同号不属于任何业务线"
            out.append({
                "document_id": doc.document_id,
                "filename": doc.filename,
                "document_type": doc.document_type,
                "contract_no": doc.contract_no,
                "role": doc.role,
                "direction": doc.direction,
                "reason": reason,
            })
        return out
