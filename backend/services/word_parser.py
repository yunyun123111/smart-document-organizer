"""Word (DOCX) 解析器（规格书第二十节）。

读取段落、标题、表格、页眉、页脚，并组合成结构化文本。
DOC（旧版二进制）V1 不直接解析，标记不支持。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from backend.services.parser_service import BaseParser, ParsedDocument
from backend.utils.logger import get_logger

logger = get_logger("services.word_parser")

# 标题样式前缀
_HEADING_PREFIXES = ("heading", "标题")


def _paragraph_to_line(para: Paragraph) -> str:
    text = para.text.strip()
    if not text:
        return ""
    style_name = (para.style.name if para.style is not None else "").lower()
    if any(style_name.startswith(p) for p in _HEADING_PREFIXES):
        return f"[标题] {text}"
    return text


def _table_to_lines(table: Table) -> tuple[list[str], list[list[str]]]:
    """把表格转为结构化行文本 + 原始二维数组。"""
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows.append(cells)
    lines = [" | ".join(cells) for cells in rows if any(cells)]
    return lines, rows


class WordParser(BaseParser):
    file_types = ("docx",)

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        doc = DocxDocument(str(path))
        lines: list[str] = []
        tables: list[list[list[str]]] = []

        # 收集页眉页脚
        header_texts: list[str] = []
        footer_texts: list[str] = []
        for section in doc.sections:
            for header in section.header.paragraphs:
                if header.text.strip():
                    header_texts.append(header.text.strip())
            for footer in section.footer.paragraphs:
                if footer.text.strip():
                    footer_texts.append(footer.text.strip())

        # 按文档顺序遍历 body 元素（段落与表格交错）
        body = doc.element.body
        for child in body.iterchildren():
            if child.tag.endswith("}p"):
                # 段落
                from docx.text.paragraph import Paragraph as P

                line = _paragraph_to_line(P(child, doc))
                if line:
                    lines.append(line)
            elif child.tag.endswith("}tbl"):
                # 表格
                tbl = Table(child, doc)
                t_lines, t_rows = _table_to_lines(tbl)
                tables.append(t_rows)
                lines.append("[表格]")
                lines.extend(t_lines)

        if header_texts:
            lines.insert(0, "[页眉] " + " | ".join(header_texts))
        if footer_texts:
            lines.append("[页脚] " + " | ".join(footer_texts))

        text = "\n".join(lines)
        parsed = ParsedDocument(
            text=text,
            metadata={
                "paragraphs": len(lines),
                "tables": len(tables),
                "sections": len(doc.sections),
            },
            tables=tables,
        )
        logger.info("Word 解析完成：段落 %d，表格 %d", len(lines), len(tables))
        return parsed
