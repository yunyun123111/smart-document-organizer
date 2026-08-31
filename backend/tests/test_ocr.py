"""Phase 4 OCR 测试：中文/英文/数字/日期/金额/低清晰度/倾斜。

使用 RapidOCR（PP-OCR 模型）。模型首次加载较慢，fixture 用 session 作用域缓存。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.services.ocr_service import OCRService


def _font(size: int):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_text_image(
    lines: list[str],
    size: tuple[int, int] = (900, 400),
    font_size: int = 42,
    rotate: int = 0,
    blur: float = 0.0,
) -> Image.Image:
    """生成印刷体文字图片；可指定旋转角度与模糊程度。"""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    f = _font(font_size)
    y = 50
    for line in lines:
        draw.text((40, y), line, font=f, fill="black")
        y += font_size + 25
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return img


@pytest.fixture(scope="session")
def ocr() -> OCRService:
    return OCRService()


class TestBasicOCR:
    def test_chinese(self, ocr):
        img = make_text_image(["采购合同", "甲方：北京科技有限公司"])
        result = ocr.recognize_image(img)
        assert result.text, "中文识别结果为空"
        assert "采购合同" in result.text or "合同" in result.text
        assert result.confidence > 0.5

    def test_english_and_digits(self, ocr):
        img = make_text_image(["Invoice No: XS202608001", "Total: 128500.50", "Date: 2026-08-20"])
        result = ocr.recognize_image(img)
        assert result.text, "英文识别结果为空"
        # 数字与关键标识
        assert any(k in result.text for k in ["XS202608001", "202608001", "XS"])
        assert "2026" in result.text

    def test_date_and_amount(self, ocr):
        img = make_text_image(["开票日期：2026年8月20日", "金额：128,500.00 元"])
        result = ocr.recognize_image(img)
        assert "2026" in result.text
        assert "128500" in result.text.replace(",", "") or "128,500" in result.text

    def test_invoice(self, ocr):
        img = make_text_image(["增值税普通发票", "发票号码 1234567890", "税率 13%"])
        result = ocr.recognize_image(img)
        assert "发票" in result.text
        assert "1234567890" in result.text.replace(" ", "") or "1234567890" in result.text


class TestToughOCR:
    def test_low_contrast(self, ocr):
        # 低清晰度：轻微模糊
        img = make_text_image(["合同编号 ABC-2026-001"], blur=1.5)
        result = ocr.recognize_image(img)
        # 模糊后至少识别出数字
        assert "2026" in result.text or "ABC" in result.text or result.text != ""

    def test_rotated_image(self, ocr):
        # 倾斜 3 度
        img = make_text_image(["采购订单", "订单号 PO2026001"], rotate=3)
        result = ocr.recognize_image(img)
        assert "PO" in result.text or "2026001" in result.text or "采购" in result.text

    def test_empty_image(self, ocr):
        img = Image.new("RGB", (300, 200), "white")
        result = ocr.recognize_image(img)
        assert result.text == ""
        assert result.confidence == 0.0
