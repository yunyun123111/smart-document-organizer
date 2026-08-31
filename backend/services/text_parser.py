"""纯文本解析器（TXT / CSV）。

TXT：直接读取文本（自动处理 UTF-8 / GBK 编码）。
CSV：解析为表格 + 结构化文本。
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from backend.services.parser_service import BaseParser, ParsedDocument
from backend.utils.logger import get_logger

logger = get_logger("services.text_parser")

_ENCODINGS = ("utf-8", "gb18030", "utf-16")


def _read_text_file(path: Path) -> str:
    """按候选编码读取文本文件，全部失败则报错。"""
    last_err: Exception | None = None
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
    # 最后尝试二进制读取后宽松解码
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"无法解码文本文件: {path.name}") from (last_err or e)


class TxtParser(BaseParser):
    file_types = ("txt",)

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        text = _read_text_file(path)
        parsed = ParsedDocument(
            text=text.strip(),
            metadata={"encoding": "auto", "lines": len(text.splitlines())},
        )
        logger.info("TXT 解析完成：%d 行，%d 字符", len(text.splitlines()), len(text))
        return parsed


class CsvParser(BaseParser):
    file_types = ("csv",)

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        raw = _read_text_file(path)
        sample = raw[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel

        rows: list[list[str]] = []
        reader = csv.reader(io.StringIO(raw), dialect)
        for row in reader:
            cells = [c.strip() for c in row]
            if any(cells):
                rows.append(cells)

        lines = [" | ".join(row) for row in rows]
        text = "\n".join(lines)
        parsed = ParsedDocument(
            text=text,
            metadata={"rows": len(rows), "columns": len(rows[0]) if rows else 0},
            tables=[rows],
        )
        logger.info("CSV 解析完成：%d 行", len(rows))
        return parsed
