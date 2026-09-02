"""OCR 结果二次清洗服务（独立模块）。

对 OCR 原始文本做更深一层的清洗，弥补模型层面误识别：
1. 数字纠错：OCR 常见混淆字母（O/0、l/I/1、S/5、B/8、G/6、Z/2、T/7）在数字上下文中纠正
2. 日期格式统一：各种写法（2026年08月14日 / 2026/8/14 / 20260814 ...）→ YYYY-MM-DD
3. 公司名模糊匹配：与已知公司名单做字符级相似度匹配，纠正 OCR 错字
4. 发票代码校验：发票号码/代码长度与数字合法性校验 + 数字纠错

所有清洗幂等、无外部依赖（difflib 为标准库），可独立使用与测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from backend.utils.logger import get_logger

logger = get_logger("services.ocr_cleaner")

# ---- 数字纠错：OCR 混淆字母 -> 数字 ----
_NUM_FIX = {
    "O": "0", "o": "0",
    "I": "1", "l": "1", "i": "1",
    "S": "5", "s": "5",
    "B": "8", "b": "8",
    "G": "6", "g": "6",
    "Z": "2", "z": "2",
    "T": "7", "t": "7",
}
_NUM_FIX_CHARS = "".join(_NUM_FIX.keys())
# 数字串内夹着混淆字母（安全纠错：数字与数字之间的一串字母必是 OCR 误识）
_DIGIT_BLOCK = re.compile(r"(?<=\d)[{}]+(?=\d)".format(_NUM_FIX_CHARS))
_CURRENCY_DIGITS = re.compile(r"(?<=[¥￥])\s*([{}]+)".format(_NUM_FIX_CHARS + "0-9"))

# ---- 日期格式统一 ----
_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_DATE_SEP = re.compile(r"(?<!\d)(\d{4})[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})日?(?!\d)")
_DATE_COMPACT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
# 简写年份（026-08-14 / 26-08-14）
_DATE_SHORT = re.compile(r"(?<!\d)(\d{2,3})[-/.月]\s*(\d{1,2})[-/.月]\s*(\d{1,2})日?(?!\d)")

# ---- 公司名 ----
_COMPANY_PATTERNS = [
    re.compile(
        r"(?:甲方|乙方|采购方|销售方|供应商|供货方|买方|卖方|付款人|收款人|开票方|购方)[:：]?\s*"
        r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,40}?(?:有限公司|有限责任公司|股份有限公司|公司|集团|厂))"
    ),
    re.compile(
        r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,30}(?:有限公司|有限责任公司|股份有限公司|集团有限公司|公司|集团|工厂|商行|事务所|合作社))"
    ),
]

# ---- 发票代码/号码校验 ----
_INVOICE_NO_CTX = re.compile(
    r"(?:发票号码|发票号|发票No\.?|Invoice\s*(?:No\.?|#)?)[:：]?\s*([0-9{}A-Za-z\-_/]{{4,30}})".format(_NUM_FIX_CHARS),
    re.IGNORECASE,
)
_INVOICE_CODE_CTX = re.compile(
    r"(?:发票代码|代码|Code)[:：]?\s*([0-9{}]{{6,14}})".format(_NUM_FIX_CHARS),
    re.IGNORECASE,
)
# 合法发票号码长度：传统 8 位；数电票 20 位；部分 12 位
_INVOICE_OK_LENGTHS = {8, 10, 12, 20}


@dataclass
class Correction:
    """一次清洗修正记录。"""
    kind: str            # number / date / company / invoice
    original: str
    corrected: str
    detail: str = ""


@dataclass
class CleanedResult:
    """二次清洗结果。"""
    text: str
    corrections: list[Correction] = field(default_factory=list)


def _norm_company(name: str) -> str:
    """公司名归一化：去空白/标点/全角。"""
    return re.sub(r"[\s\u3000，,。；;：:（）()\-_/]", "", name)


class OCRCleaner:
    """OCR 二次清洗入口。"""

    def clean(self, text: str | None, known_companies: list[str] | None = None) -> CleanedResult:
        """完整二次清洗流程（幂等）。"""
        text = text or ""
        corr: list[Correction] = []

        text, c1 = self._fix_numbers(text)
        corr.extend(c1)
        text, c2 = self._unify_dates(text)
        corr.extend(c2)
        if known_companies:
            text, c3 = self._fix_company_names(text, known_companies)
            corr.extend(c3)
        text, c4 = self._fix_invoice_code(text)
        corr.extend(c4)

        if corr:
            logger.info("OCR 二次清洗修正 %d 处: %s", len(corr),
                        "; ".join(f"{c.kind}:{c.original}->{c.corrected}" for c in corr[:20]))
        return CleanedResult(text=text, corrections=corr)

    # ---------- 1) 数字纠错 ----------
    def _fix_numbers(self, text: str) -> tuple[str, list[Correction]]:
        corr: list[Correction] = []

        def _repl_inline(m: re.Match) -> str:
            seg = m.group(0)
            fixed = "".join(_NUM_FIX.get(ch, ch) for ch in seg)
            if fixed != seg:
                corr.append(Correction("number", seg, fixed, "数字串内混淆字母"))
            return fixed

        text = _DIGIT_BLOCK.sub(_repl_inline, text)

        def _repl_currency(m: re.Match) -> str:
            seg = m.group(1)
            out = []
            for ch in seg:
                fixed = _NUM_FIX.get(ch, ch)
                if fixed != ch:
                    corr.append(Correction("number", ch, fixed, "货币符号后数字"))
                out.append(fixed)
            return "¥" + "".join(out) if text[m.start():m.start()+1] == "¥" else "￥" + "".join(out)

        # 货币符号后的数字串（保留原符号）
        def _repl_curr_preserve(m: re.Match) -> str:
            sym = m.group(0)[0]
            seg = m.group(1)
            out = []
            for ch in seg:
                fixed = _NUM_FIX.get(ch, ch)
                if fixed != ch:
                    corr.append(Correction("number", ch, fixed, "货币符号后数字"))
                out.append(fixed)
            return sym + "".join(out)

        text = _CURRENCY_DIGITS.sub(_repl_curr_preserve, text)
        return text, corr

    # ---------- 2) 日期格式统一 ----------
    def _unify_dates(self, text: str) -> tuple[str, list[Correction]]:
        corr: list[Correction] = []

        def _norm(y: str, mo: str, d: str) -> str:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

        def _rep_cn(m: re.Match) -> str:
            v = _norm(m.group(1), m.group(2), m.group(3))
            if v != m.group(0):
                corr.append(Correction("date", m.group(0), v, "中文日期统一"))
            return v

        text = _DATE_CN.sub(_rep_cn, text)

        def _rep_sep(m: re.Match) -> str:
            v = _norm(m.group(1), m.group(2), m.group(3))
            if v != m.group(0):
                corr.append(Correction("date", m.group(0), v, "分隔符日期统一"))
            return v

        text = _DATE_SEP.sub(_rep_sep, text)

        def _rep_short(m: re.Match) -> str:
            y = m.group(1)
            if len(y) == 4:
                yy = y
            elif len(y) == 3:
                yy = "2" + y   # 026 -> 2026
            else:
                yy = "20" + y  # 26 -> 2026
            v = _norm(yy, m.group(2), m.group(3))
            if v != m.group(0):
                corr.append(Correction("date", m.group(0), v, "简写年份日期统一"))
            return v

        text = _DATE_SHORT.sub(_rep_short, text)

        def _rep_compact(m: re.Match) -> str:
            y, mo, d = m.group(1), m.group(2), m.group(3)
            if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
                return m.group(0)
            v = f"{y}-{mo}-{d}"
            corr.append(Correction("date", m.group(0), v, "紧凑日期展开"))
            return v

        text = _DATE_COMPACT.sub(_rep_compact, text)
        return text, corr

    # ---------- 3) 公司名模糊匹配 ----------
    def _fix_company_names(self, text: str, known: list[str]) -> tuple[str, list[Correction]]:
        corr: list[Correction] = []
        # 归一化已知名单，建立 原样->归一 映射
        known_map: dict[str, str] = {}
        for name in known:
            if not name or len(name) < 2:
                continue
            known_map[name] = _norm_company(name)
        if not known_map:
            return text, corr

        # 找出文本中的候选公司名
        seen: set[int] = set()
        for pat in _COMPANY_PATTERNS:
            for m in pat.finditer(text):
                cand = m.group(1).strip()
                norm_cand = _norm_company(cand)
                if len(norm_cand) < 2 or m.start() in seen:
                    continue
                seen.add(m.start())
                best_name, best_score = self._best_match(norm_cand, known_map)
                if best_name and best_score >= 0.85 and best_score < 1.0:
                    # 仅当确实不同才替换（完全相同跳过）
                    text = text[: m.start()] + best_name + text[m.end():]
                    corr.append(Correction("company", cand, best_name,
                                           f"模糊匹配 {best_score:.2f}"))
        return text, corr

    @staticmethod
    def _best_match(norm_cand: str, known_map: dict[str, str]) -> tuple[str | None, float]:
        """在已知公司名单中找最相似者。"""
        best_name, best_score = None, 0.0
        for name, norm in known_map.items():
            if abs(len(norm) - len(norm_cand)) > max(3, len(norm_cand) // 3):
                continue
            ratio = SequenceMatcher(None, norm_cand, norm).ratio()
            if ratio > best_score:
                best_score, best_name = ratio, name
        return (best_name, best_score) if best_name else (None, 0.0)

    # ---------- 4) 发票代码校验 ----------
    def _fix_invoice_code(self, text: str) -> tuple[str, list[Correction]]:
        corr: list[Correction] = []

        def _fix_digits(seg: str) -> tuple[str, list[Correction]]:
            out = []
            for ch in seg:
                if ch.isdigit():
                    out.append(ch)
                elif ch in _NUM_FIX:
                    fixed = _NUM_FIX[ch]
                    corr.append(Correction("invoice", ch, fixed, "发票号码数字纠错"))
                    out.append(fixed)
                else:
                    out.append(ch)
            return "".join(out), corr

        # 发票号码：纠错 + 长度校验
        for m in _INVOICE_NO_CTX.finditer(text):
            raw = m.group(1)
            fixed, _ = _fix_digits(raw)
            if fixed != raw:
                text = text[: m.start(1)] + fixed + text[m.end(1):]
                corr.append(Correction("invoice", raw, fixed, "发票号码"))
            digits = re.sub(r"[^\d]", "", fixed)
            if digits and len(digits) not in _INVOICE_OK_LENGTHS:
                logger.info("发票号码长度异常: %s（%d 位）", digits, len(digits))

        # 发票代码：纠错 + 长度校验
        for m in _INVOICE_CODE_CTX.finditer(text):
            raw = m.group(1)
            fixed, _ = _fix_digits(raw)
            if fixed != raw:
                text = text[: m.start(1)] + fixed + text[m.end(1):]
                corr.append(Correction("invoice", raw, fixed, "发票代码"))
            digits = re.sub(r"[^\d]", "", fixed)
            if digits and len(digits) not in (8, 10, 12):
                logger.info("发票代码长度异常: %s（%d 位）", digits, len(digits))

        return text, corr


# 全局单例
ocr_cleaner = OCRCleaner()


def get_known_companies(db) -> list[str]:
    """从已归档文档的字段中聚合高频公司名（company / seller）。

    供公司名模糊匹配使用；按出现频次降序，取前 200。
    """
    from backend.models import Document, DocumentField

    try:
        rows = (
            db.query(DocumentField.field_value, DocumentField.document_id)
            .filter(DocumentField.field_name.in_(("company", "seller")))
            .all()
        )
        freq: dict[str, int] = {}
        for value, _doc_id in rows:
            v = (value or "").strip()
            if len(v) >= 4 and ("公司" in v or "厂" in v or "集团" in v or "事务所" in v):
                freq[v] = freq.get(v, 0) + 1
        return [k for k, _ in sorted(freq.items(), key=lambda x: -x[1])][:200]
    except Exception:
        logger.exception("聚合已知公司名失败")
        return []
