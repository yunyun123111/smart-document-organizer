"""PDF 解析器（规格书第十八节）。

流程：
  PDF → 检测文本 → 文本长度 > 阈值？ → 是 → 提取文本
                                    → 否 → 标记需要 OCR（扫描件）
同时记录：是否 OCR、页数、文本长度。
"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from backend.services.parser_service import (
    PDF_TEXT_THRESHOLD_PER_PAGE,
    BaseParser,
    ParsedDocument,
    SOURCE_TEXT,
)
from backend.utils.logger import get_logger

logger = get_logger("services.pdf_parser")


class PdfParser(BaseParser):
    file_types = ("pdf",)

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        doc = fitz.open(str(path))
        try:
            page_count = doc.page_count
            pages_text: list[str] = []
            images: list[dict] = []

            for page_index in range(page_count):
                page = doc.load_page(page_index)
                text = page.get_text("text")
                pages_text.append(text)
                # 记录页内图像（扫描件通常含大量图片）
                for img_index, img in enumerate(page.get_images(full=True)):
                    xref = img[0]
                    images.append({"page": page_index + 1, "xref": xref, "index": img_index})

            text = "\n".join(pages_text).strip()
            avg_per_page = len(text) / page_count if page_count else 0

            parsed = ParsedDocument(
                text=text,
                metadata={
                    "page_count": page_count,
                    "needs_ocr": avg_per_page < PDF_TEXT_THRESHOLD_PER_PAGE,
                    "avg_chars_per_page": round(avg_per_page, 1),
                },
                pages=page_count,
                images=images,
                source=SOURCE_TEXT,
            )
            parsed.needs_ocr = parsed.metadata["needs_ocr"]
            if parsed.needs_ocr:
                parsed.text = ""  # 扫描件不保留零散文本，交由 OCR
                logger.info("PDF 判定为扫描件（页均 %s 字符），标记 OCR", round(avg_per_page, 1))
            else:
                logger.info(
                    "PDF 解析完成：%d 页，文本 %d 字符", page_count, len(parsed.text)
                )
            return parsed
        finally:
            doc.close()
