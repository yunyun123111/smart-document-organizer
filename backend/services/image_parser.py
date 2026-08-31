"""图片解析器（规格书第十九节）。

流程：图片 → 读取 → 旋转校正 → 图像增强 → OCR → 文本。
P3 阶段完成"读取 + 元数据 + 标记需要 OCR"；OCR 识别由 OCRService（P4）补充。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from backend.services.parser_service import BaseParser, ParsedDocument
from backend.utils.logger import get_logger

logger = get_logger("services.image_parser")


class ImageParser(BaseParser):
    file_types = ("jpg", "png")

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        with Image.open(path) as img:
            # EXIF 方向校正
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            mode = img.mode
            fmt = img.format or path.suffix.lstrip(".").upper()

        parsed = ParsedDocument(
            text="",
            metadata={
                "width": width,
                "height": height,
                "mode": mode,
                "format": fmt,
            },
            pages=1,
            images=[{"page": 1, "path": str(path), "width": width, "height": height}],
            needs_ocr=True,  # 图片必须 OCR（P4 接入）
        )
        logger.info("图片解析完成：%sx%s, mode=%s，标记 OCR", width, height, mode)
        return parsed
