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
from html import escape

from pypinyin import Style, lazy_pinyin
from sqlalchemy import text

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


# ---- 正文全文检索（OCR/解析出的 extracted_text）----
# FTS5 trigram 要求关键词 >= 3 字符；更短的中文词（"发票""合同"）走 instr 扫描
_FTS_MIN_LEN = 3
_FTS_ID_LIMIT = 5000
_SNIPPET_RADIUS = 40


def _fts_query(keyword: str) -> str:
    """包成 FTS5 短语，规避 - * : ( ) 等查询语法字符。"""
    return '"' + keyword.replace('"', '""') + '"'


def _instr_doc_ids(db, keyword: str) -> set[int]:
    """字面子串匹配（instr 不受 LIKE 的 % / _ 通配影响）。"""
    rows = db.execute(
        text("SELECT id FROM documents WHERE instr(extracted_text, :kw) > 0 LIMIT :lim"),
        {"kw": keyword, "lim": _FTS_ID_LIMIT},
    ).fetchall()
    return {r[0] for r in rows}


def fulltext_doc_ids(db, keyword: str) -> set[int]:
    """正文命中的文档 id 集合；索引不可用时自动降级为扫描。"""
    kw = (keyword or "").strip()
    if not kw:
        return set()
    if len(kw) < _FTS_MIN_LEN:
        return _instr_doc_ids(db, kw)
    try:
        rows = db.execute(
            text("SELECT rowid FROM document_fts WHERE document_fts MATCH :q LIMIT :lim"),
            {"q": _fts_query(kw), "lim": _FTS_ID_LIMIT},
        ).fetchall()
        return {int(r[0]) for r in rows}
    except Exception:  # noqa: BLE001
        return _instr_doc_ids(db, kw)


def _hit_snippet(body: str, kw: str, radius: int = _SNIPPET_RADIUS) -> str | None:
    """截取命中片段：非命中部分 HTML 转义，命中包 <mark>（前端 v-html 安全）。"""
    idx = body.find(kw)
    if idx < 0:
        return None
    start = max(0, idx - radius)
    end = min(len(body), idx + len(kw) + radius)
    return (
        ("…" if start > 0 else "")
        + escape(body[start:idx])
        + "<mark>"
        + escape(kw)
        + "</mark>"
        + escape(body[idx + len(kw) : end])
        + ("…" if end < len(body) else "")
    )


def fulltext_snippets(db, keyword: str, doc_ids: list[int]) -> dict[int, str]:
    """返回 {doc_id: 正文命中片段}，供列表高亮。只处理当页文档，代价可控。"""
    kw = (keyword or "").strip()
    ids = [int(i) for i in doc_ids if i is not None]
    if not kw or not ids:
        return {}
    rows = db.execute(
        text(
            "SELECT id, extracted_text FROM documents WHERE id IN ("
            + ",".join(str(i) for i in ids)
            + ")"
        )
    ).fetchall()
    out: dict[int, str] = {}
    for doc_id, body in rows:
        snip = _hit_snippet(body or "", kw)
        if snip:
            out[int(doc_id)] = snip
    return out


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
