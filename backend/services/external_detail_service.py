"""外部明细表导入与汇总（P1）。

解决的问题：台账里只有汇总数，明细在用户另一张表里（例如下游回款明细）。
以前这类数据只能人工比对，现在导入本表后按业务线挂载，供页面/导出做参考比对。

**不参与判定**：本服务不改对账状态、不产生差异。
下游回款"一个客户挂多条业务线、款拆不开"的口径还没定，若参与判定会把
"拆不开"误报成"回款不足"，比不判更糟。口径确认后再接进规则。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models import ExternalDetail, KIND_PAYMENT, KIND_RECEIPT

# 表头别名：用户表不一定叫「业务线号」，按常见写法兜住
_BUSINESS_ALIASES = ("业务线号", "业务线编号", "业务线", "业务编号", "业务线id", "business_no")
_DATE_ALIASES = ("日期", "发生日期", "交易日期", "回款日期", "付款日期", "date", "occurred_on")
_AMOUNT_ALIASES = ("金额", "回款金额", "收款金额", "付款金额", "发生额", "amount")
_KIND_ALIASES = ("类型", "方向", "收支", "收付", "kind")
_NOTE_ALIASES = ("备注", "摘要", "说明", "note", "memo")

_RECEIPT_WORDS = ("回款", "收款", "收入", "入账", "收款", "receive", "receipt")
_PAYMENT_WORDS = ("付款", "支付", "支出", "付出", "pay", "payment")

_EXCEL_EPOCH = datetime(1899, 12, 30)


class ExternalDetailError(Exception):
    """导入失败（文件损坏 / 找不到必要列）。"""


def _norm_header(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).lower().replace("（", "(").replace("）", ")")


def _pick_column(headers: dict[str, int], aliases: tuple[str, ...], mapping: dict, key: str) -> Optional[int]:
    """优先用用户显式映射，其次按别名匹配表头。"""
    explicit = str(mapping.get(key) or "").strip()
    if explicit:
        idx = headers.get(_norm_header(explicit))
        if idx is not None:
            return idx
    for alias in aliases:
        idx = headers.get(_norm_header(alias))
        if idx is not None:
            return idx
    return None


def parse_amount(value: Any) -> Optional[Decimal]:
    """把单元格里的金额转成 Decimal。空值返回 None（调用方跳过该行）。

    容忍：千分位、货币符号、全角、括号负数、前后空格。
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("，", "")
    text = re.sub(r"[¥￥$ \u3000]", "", text)
    if not text or text in {"-", "—"}:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if negative:
        amount = -abs(amount)
    return amount.quantize(Decimal("0.01"))


def parse_date(value: Any) -> Optional[date]:
    """日期解析：datetime / date / 字符串多格式 / Excel 序列号。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        return (_EXCEL_EPOCH + timedelta(days=float(value))).date()
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_kind(value: Any, amount: Optional[Decimal]) -> str:
    """收/付方向：有词按词，没词按金额正负，都没有按收款（下游回款表主场景）。"""
    text = _norm_header(value)
    if text:
        if any(w in text for w in _PAYMENT_WORDS):
            return KIND_PAYMENT
        if any(w in text for w in _RECEIPT_WORDS):
            return KIND_RECEIPT
    if amount is not None and amount < 0:
        return KIND_PAYMENT
    return KIND_RECEIPT


def parse_workbook(path: str | Path, mapping: Optional[dict] = None) -> tuple[list[dict], dict]:
    """解析外部明细表，返回 (行列表, 统计)。

    只用第一个 sheet；表头在第一行。找不到业务线号或金额列直接报错——
    静默导进去一批"匹配不到"的行，比不导更费人。
    """
    mapping = mapping or {}
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl 对损坏文件抛的异常类型不统一
        raise ExternalDetailError(f"无法打开表格：{exc}") from exc
    ws = wb[wb.sheetnames[0]]

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration as exc:
        raise ExternalDetailError("表格是空的") from exc

    headers: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        key = _norm_header(cell)
        if key and key not in headers:
            headers[key] = idx

    col_business = _pick_column(headers, _BUSINESS_ALIASES, mapping, "business_no")
    col_amount = _pick_column(headers, _AMOUNT_ALIASES, mapping, "amount")
    if col_business is None:
        raise ExternalDetailError("表头里找不到「业务线号」列，请在导入时指定列名")
    if col_amount is None:
        raise ExternalDetailError("表头里找不到「金额」列，请在导入时指定列名")
    col_date = _pick_column(headers, _DATE_ALIASES, mapping, "date")
    col_kind = _pick_column(headers, _KIND_ALIASES, mapping, "kind")
    col_note = _pick_column(headers, _NOTE_ALIASES, mapping, "note")

    records: list[dict] = []
    skipped = 0
    unmatched = 0
    for raw in rows_iter:
        if raw is None:
            skipped += 1
            continue
        business = str(raw[col_business] or "").strip().upper() if col_business < len(raw) else ""
        amount = parse_amount(raw[col_amount] if col_amount < len(raw) else None)
        if not business or amount is None:
            skipped += 1
            continue
        occurred = parse_date(raw[col_date]) if col_date is not None and col_date < len(raw) else None
        kind = normalize_kind(raw[col_kind] if col_kind is not None and col_kind < len(raw) else "", amount)
        note = str(raw[col_note] or "").strip() if col_note is not None and col_note < len(raw) else ""
        records.append({
            "business_no": business,
            "occurred_on": occurred,
            "amount": abs(amount),
            "kind": kind,
            "note": note[:500],
        })
    wb.close()

    stats = {
        "rows": len(records),
        "skipped": skipped,
        "unmatched": unmatched,
        "columns": {
            "business_no": header_row[col_business] if col_business is not None else "",
            "amount": header_row[col_amount] if col_amount is not None else "",
            "date": header_row[col_date] if col_date is not None and col_date < len(header_row) else "",
            "kind": header_row[col_kind] if col_kind is not None and col_kind < len(header_row) else "",
        },
    }
    return records, stats


def import_details(
    db: Session,
    path: str | Path,
    filename: str = "",
    mapping: Optional[dict] = None,
) -> dict:
    """导入一批外部明细。整体一个批次，返回批次号与统计。"""
    records, stats = parse_workbook(path, mapping)
    batch_no = f"EXT{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
    for item in records:
        db.add(ExternalDetail(
            batch_no=batch_no,
            business_no=item["business_no"],
            occurred_on=item["occurred_on"],
            amount=item["amount"],
            kind=item["kind"],
            note=item["note"],
            source_filename=filename or Path(path).name,
            imported_at=datetime.now(),
        ))
    db.commit()
    return {
        "batch_no": batch_no,
        "imported": len(records),
        "skipped": stats["skipped"],
        "source_filename": filename or Path(path).name,
        "columns": stats["columns"],
        "message": f"已导入 {len(records)} 行，跳过 {stats['skipped']} 行（缺业务线号或金额）",
    }


def list_details(
    db: Session,
    business_no: str = "",
    batch_no: str = "",
    limit: int = 500,
) -> list[ExternalDetail]:
    stmt = select(ExternalDetail).order_by(ExternalDetail.occurred_on.desc(), ExternalDetail.id.desc())
    if business_no:
        stmt = stmt.where(ExternalDetail.business_no == business_no.strip().upper())
    if batch_no:
        stmt = stmt.where(ExternalDetail.batch_no == batch_no)
    stmt = stmt.limit(max(1, min(limit, 5000)))
    return list(db.execute(stmt).scalars().all())


def list_batches(db: Session) -> list[dict]:
    rows = db.execute(select(ExternalDetail)).scalars().all()
    batches: dict[str, dict] = {}
    for row in rows:
        item = batches.setdefault(row.batch_no, {
            "batch_no": row.batch_no,
            "source_filename": row.source_filename,
            "imported_at": row.imported_at.strftime("%Y-%m-%d %H:%M:%S") if row.imported_at else "",
            "rows": 0,
            "receipt": Decimal("0"),
            "payment": Decimal("0"),
        })
        item["rows"] += 1
        if row.kind == KIND_PAYMENT:
            item["payment"] += row.amount
        else:
            item["receipt"] += row.amount
    for item in batches.values():
        item["receipt"] = f"{item['receipt']:.2f}"
        item["payment"] = f"{item['payment']:.2f}"
    return sorted(batches.values(), key=lambda x: x["imported_at"], reverse=True)


def delete_batch(db: Session, batch_no: str) -> int:
    result = db.execute(delete(ExternalDetail).where(ExternalDetail.batch_no == batch_no))
    db.commit()
    return int(result.rowcount or 0)


def aggregate_by_business(db: Session) -> dict[str, dict]:
    """按业务线汇总外部明细：收款合计 / 付款合计 / 笔数 / 最近日期。"""
    rows = db.execute(select(ExternalDetail)).scalars().all()
    result: dict[str, dict] = {}
    for row in rows:
        item = result.setdefault(row.business_no, {
            "business_no": row.business_no,
            "receipt_total": Decimal("0"),
            "payment_total": Decimal("0"),
            "count": 0,
            "last_date": None,
        })
        item["count"] += 1
        if row.kind == KIND_PAYMENT:
            item["payment_total"] += row.amount
        else:
            item["receipt_total"] += row.amount
        if row.occurred_on and (item["last_date"] is None or row.occurred_on > item["last_date"]):
            item["last_date"] = row.occurred_on
    for item in result.values():
        item["receipt_total"] = f"{item['receipt_total']:.2f}"
        item["payment_total"] = f"{item['payment_total']:.2f}"
        item["last_date"] = item["last_date"].isoformat() if item["last_date"] else ""
    return result


def mapping_from_json(text: str) -> dict:
    if not text or not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ExternalDetailError(f"列映射不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ExternalDetailError("列映射必须是 JSON 对象，例如 {\"business_no\":\"业务线号\"}")
    return data
