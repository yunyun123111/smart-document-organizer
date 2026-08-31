"""文本预处理服务（规格书第二十二节）。

提取文本后的清洗与规范化：
- 去除多余空格
- 统一换行
- 去除重复字符
- 统一日期格式（2026 年 08 月 20 日 → 2026-08-20）
- 统一金额格式（人民币 128,500.00 元 → 128500）
"""
from __future__ import annotations

import re

from backend.services.parser_service import ParsedDocument

logger = None  # 日志在需要时通过 backend.utils.logger 获取

# 中文日期：2026 年 08 月 20 日 / 2026年8月20日 / 2026-08-20 / 2026/8/20 / 2026.08.20
_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"),
    re.compile(r"(\d{4})年(\d{1,2})月"),
]

# 金额：必须带货币符号（人民币/RMB/¥/￥）或"元"或小数位，避免误伤日期/编号等纯数字
_AMOUNT_PATTERNS = [
    # 带货币符号：人民币 128,500.00 元 / ¥128500
    re.compile(r"(?:人民币|RMB|¥|￥)\s*([\d,]+(?:\.\d{1,2})?)\s*元?"),
    # 带小数位 + 元：128,500.00 元
    re.compile(r"([\d,]+\.\d{1,2})\s*元"),
    # 纯数字 + 元：128500 元
    re.compile(r"([\d,]+)\s*元"),
]

# 重复字符：超过 3 次相同的连续字符折叠为 3 个（如 "！！！！！！！" → "！！！"）
_DUP_CHARS = re.compile(r"(\S)\1{3,}")


def _normalize_whitespace(text: str) -> str:
    """去除多余空格：连续空格/全角空格合并，保留中英文单空格。"""
    text = text.replace("\u3000", " ")  # 全角空格
    return re.sub(r"[ \t]+", " ", text)


def _normalize_newlines(text: str) -> str:
    """统一换行为 \n，去除多余空行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text)


def _remove_duplicate_chars(text: str) -> str:
    """去除连续重复 3 次以上的字符。"""
    return _DUP_CHARS.sub(lambda m: m.group(1) * 3, text)


def _normalize_dates(text: str) -> str:
    """统一日期格式为 YYYY-MM-DD / YYYY-MM。"""
    def repl(m: re.Match) -> str:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{y}-{int(mo):02d}-{int(d):02d}"

    text = _DATE_PATTERNS[0].sub(repl, text)
    text = _DATE_PATTERNS[1].sub(repl, text)
    text = _DATE_PATTERNS[2].sub(lambda m: f"{m.group(1)}-{int(m.group(2)):02d}", text)
    return text


def _normalize_amounts(text: str) -> str:
    """金额去货币符号、千分位、保留数字本体（仅识别带货币标识/元/小数的金额）。"""
    def repl(m: re.Match) -> str:
        raw = m.group(1)
        digits = raw.replace(",", "")
        return digits

    for pattern in _AMOUNT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def clean_text(text: str) -> str:
    """完整清洗流程。"""
    if not text:
        return ""
    text = _normalize_newlines(text)
    text = _normalize_whitespace(text)
    text = _remove_duplicate_chars(text)
    text = _normalize_dates(text)
    text = _normalize_amounts(text)
    return text.strip()


def clean_parsed_document(parsed: ParsedDocument) -> ParsedDocument:
    """清洗解析结果中的文本，并同步 tables 内单元格文本。"""
    parsed.text = clean_text(parsed.text)
    if parsed.tables:
        parsed.tables = [
            [[clean_text(cell) for cell in row] for row in table]
            for table in parsed.tables
        ]
    return parsed
