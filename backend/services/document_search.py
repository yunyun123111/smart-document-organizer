"""文档库智能搜索服务：拼音搜索 / 错别字容错 / 字段过滤 / 自动补全。

- 拼音搜索：输入全拼（fapiao）或首字母（fp）可匹配中文类型、标题、文件名
- 错别字容错：对文档类型枚举做字符相似度匹配（difflib）
- 字段过滤：合同号模糊、金额区间、日期范围（date 字段或 created_at）
- 搜索建议：类型 / 公司 / 合同号 / 文件名 自动补全
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

from pypinyin import Style, lazy_pinyin

from backend.models import Document, DocumentField

_ASCII_RE = re.compile(r"^[\x00-\x7f\s]+$")

# 金额/日期相关的字段名
_AMOUNT_FIELDS = ("amount", "total_amount", "tax_amount", "price")
_DATE_FIELDS = ("date", "invoice_date", "ship_date", "bill_date")
_CN_FIELD_LABELS = {
    "company": "公司",
    "seller": "销售方",
    "buyer": "采购方",
    "contract_no": "合同号",
    "invoice_no": "发票号",
    "ship_name": "船名",
    "material": "物料",
    "date": "日期",
    "amount": "金额",
}


@lru_cache(maxsize=2048)
def _py(text: str) -> tuple[str, str]:
    """返回文本的 (全拼串, 首字母串)。"""
    full = "".join(lazy_pinyin(text)).lower()
    init = "".join(lazy_pinyin(text, style=Style.FIRST_LETTER)).lower()
    return full, init


def is_ascii(keyword: str) -> bool:
    return bool(_ASCII_RE.match(keyword))


def keyword_pinyin(keyword: str) -> tuple[str, str]:
    """把搜索词转成 (全拼, 首字母)，非 ASCII 关键词返回空。"""
    if not is_ascii(keyword):
        return "", ""
    return _py(keyword)


def _pinyin_match_doc_ids(db, kw_full: str, kw_init: str) -> set[int]:
    """全表对 类型/标题/文件名 转拼音，返回命中文档 id。"""
    ids: set[int] = set()
    rows = (
        db.query(
            Document.id, Document.document_type, Document.title,
            Document.original_filename, Document.current_filename,
        )
        .filter(Document.status != "recycled")
        .all()
    )
    for row in rows:
        text = " ".join(
            filter(None, [row.document_type, row.title, row.original_filename, row.current_filename])
        )
        if not text:
            continue
        # 只对含中文的候选做拼音匹配
        if not re.search(r"[\u4e00-\u9fa5]", text):
            continue
        full, init = _py(text)
        if (kw_full and kw_full in full) or (kw_init and kw_init in init):
            ids.add(row.id)
    return ids


def _chars_diff(a: str, b: str) -> int | None:
    """逐字差异数（仅等长时返回，否则 None）。"""
    if len(a) != len(b):
        return None
    return sum(1 for x, y in zip(a, b) if x != y)


def fuzzy_match_types(db, keyword: str) -> set[str]:
    """错别字容错：对文档类型枚举做相似度匹配。"""
    types = {t for (t,) in db.query(Document.document_type).filter(Document.status != "recycled").distinct() if t}
    if not types:
        return set()
    kw_full, kw_init = keyword_pinyin(keyword)
    out: set[str] = set()
    for t in types:
        # 子串
        if keyword in t:
            out.add(t)
            continue
        # 等长汉字容错：2~6 字且只差 1 个字符（如 合童→合同、发飘→发票）
        diff = _chars_diff(keyword, t)
        if diff is not None and 2 <= len(t) <= 6 and diff <= 1:
            out.add(t)
            continue
        if SequenceMatcher(None, keyword, t).ratio() >= 0.7:
            out.add(t)
            continue
        # 拼音（仅当关键词为拼音时）
        if kw_full:
            t_full, t_init = _py(t)
            if (kw_full and kw_full in t_full) or (kw_init and kw_init in t_init):
                out.add(t)
    return out


def match_field_ids(
    db,
    field_names: tuple[str, ...],
    like: str | None = None,
    min_val: float | None = None,
    max_val: float | None = None,
    start: str | None = None,
    end: str | None = None,
) -> set[int]:
    """按 DocumentField 过滤：like 模糊 / 数值区间 / 字符串区间。"""
    q = db.query(DocumentField.document_id, DocumentField.field_value).filter(
        DocumentField.field_name.in_(field_names)
    )
    rows = q.all()
    matched: set[int] = set()
    for doc_id, val in rows:
        v = (val or "").strip()
        if not v:
            continue
        if like and like not in v:
            continue
        # 数值区间（仅当能转 float）
        if min_val is not None or max_val is not None:
            try:
                num = float(v.replace(",", ""))
            except ValueError:
                continue
            if min_val is not None and num < min_val:
                continue
            if max_val is not None and num > max_val:
                continue
            matched.add(doc_id)
            continue
        # 字符串区间（日期 YYYY-MM-DD 可直接字符串比较）
        if start is not None and v < start:
            continue
        if end is not None and v > end:
            continue
        matched.add(doc_id)
    return matched


def build_keyword_conditions(db, keyword: str) -> list:
    """构造 keyword 的 SQL 条件（or_ 用）：子串 + 字段 + 拼音 + 容错。"""
    from sqlalchemy import or_  # noqa: F401  (提示用法)

    like = f"%{keyword}%"
    field_ids = (
        db.query(DocumentField.document_id).filter(DocumentField.field_value.like(like))
    )
    conds = [
        Document.original_filename.like(like),
        Document.current_filename.like(like),
        Document.document_type.like(like),
        Document.id.in_(field_ids),
    ]
    kw_full, kw_init = keyword_pinyin(keyword)
    if kw_full:
        ids = _pinyin_match_doc_ids(db, kw_full, kw_init)
        if ids:
            conds.append(Document.id.in_(ids))
    fz = fuzzy_match_types(db, keyword)
    if fz:
        conds.append(Document.document_type.in_(fz))
    return conds


def suggest(db, keyword: str, limit: int = 20) -> list[dict]:
    """搜索建议：类型 / 公司 / 合同号 / 发票号 / 船名 / 物料 / 文件名。"""
    kw = keyword.strip()
    if not kw:
        return []
    out: list[dict] = []
    seen: set[tuple] = set()
    like = f"%{kw}%"

    # 类型
    kw_full, kw_init = keyword_pinyin(kw)
    types = sorted({t for (t,) in db.query(Document.document_type).filter(Document.status != "recycled").distinct() if t})
    for t in types:
        hit = kw in t or SequenceMatcher(None, kw, t).ratio() >= 0.7
        if not hit and kw_full:
            t_full, t_init = _py(t)
            hit = (kw_full in t_full) or (kw_init in t_init)
        if hit and (("类型", t) not in seen):
            seen.add(("类型", t))
            out.append({"type": "类型", "text": t})

    # 字段值（公司/合同号/发票号/船名/物料）
    field_names = ("company", "seller", "buyer", "contract_no",
                   "invoice_no", "ship_name", "material")
    rows = (
        db.query(DocumentField.field_name, DocumentField.field_value)
        .filter(
            DocumentField.field_name.in_(field_names),
            DocumentField.field_value.like(like),
        )
        .distinct()
        .limit(30)
        .all()
    )
    for name, val in rows:
        v = (val or "").strip()
        if not v:
            continue
        label = _CN_FIELD_LABELS.get(name, name)
        if (label, v) not in seen:
            seen.add((label, v))
            out.append({"type": label, "text": v})

    # 文件名
    filenames = (
        db.query(Document.original_filename)
        .filter(
            Document.original_filename.like(like),
            Document.status != "recycled",
        )
        .order_by(Document.created_at.desc())
        .limit(10)
        .all()
    )
    for (f,) in filenames:
        if not f:
            continue
        if ("文件名", f) not in seen:
            seen.add(("文件名", f))
            out.append({"type": "文件名", "text": f})

    return out[:limit]
