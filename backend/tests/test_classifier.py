"""Phase 分类引擎测试：完整识别链路（解析→OCR→规则→字段→置信度→建议）。"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from backend.database import seed_default_categories, seed_default_rules
from backend.services.classifier import ClassifierService


def _make_contract_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "销售合同",
        "合同编号：XS202608001",
        "甲方：ABC有限公司",
        "乙方：DEF有限公司",
        "合同金额：人民币 128,500.00 元",
        "签订日期：2026年08月20日",
        "本合同由甲乙双方根据《中华人民共和国民法典》订立。",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s")
        y += 24
    doc.save(str(path))
    doc.close()


def _font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_invoice_image(path: Path) -> None:
    img = Image.new("RGB", (800, 250), "white")
    draw = ImageDraw.Draw(img)
    draw.text((30, 40), "增值税专用发票", font=_font(48), fill="black")
    draw.text((30, 120), "发票号码：123456789012", font=_font(36), fill="black")
    draw.text((30, 190), "金额：128,500.00 元", font=_font(36), fill="black")
    img.save(str(path))


class TestClassifier:
    def test_analyze_contract(self, seeded_db, tmp_path):
        pdf = tmp_path / "合同.pdf"
        _make_contract_pdf(pdf)
        svc = ClassifierService(seeded_db)
        result = svc.analyze(pdf)

        assert not result.error
        assert result.document_type == "销售合同"
        assert result.suggested_category == "合同/销售合同"
        assert result.confidence.score >= 0.6
        assert result.decision in ("auto", "review")
        # 字段提取
        assert result.field_values.get("contract_no") == "XS202608001"
        assert result.field_values.get("company") == "ABC有限公司"
        assert result.field_values.get("date") == "2026-08-20"
        # 建议文件名含模板变量值
        assert "XS202608001" in result.suggested_filename
        assert result.suggested_filename.endswith(".pdf")

    def test_analyze_invoice_image_with_ocr(self, seeded_db, tmp_path):
        img = tmp_path / "发票.png"
        _make_invoice_image(img)
        svc = ClassifierService(seeded_db)
        result = svc.analyze(img)

        assert result.needs_ocr is True
        assert "发票" in result.text
        assert result.document_type == "发票"
        assert result.suggested_category == "财务/发票"

    def test_analyze_unknown_file(self, seeded_db, tmp_path):
        p = tmp_path / "random.txt"
        p.write_text("这是一段完全随意的日记内容", encoding="utf-8")
        svc = ClassifierService(seeded_db)
        result = svc.analyze(p)
        assert result.error == ""
        assert result.decision == "reject"  # 无规则无字段 -> 无法判断
        assert result.suggested_category == "其他"

    def test_analyze_missing_file(self, seeded_db, tmp_path):
        svc = ClassifierService(seeded_db)
        result = svc.analyze(tmp_path / "nope.pdf")
        assert result.error
        assert result.decision == "reject"

    def test_to_dict(self, seeded_db, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("销售合同 合同编号 AB-001", encoding="utf-8")
        svc = ClassifierService(seeded_db)
        result = svc.analyze(p)
        d = result.to_dict()
        assert "document_type" in d
        assert "suggested_filename" in d
