"""核心文档指纹算法服务测试（V5 步骤 3 + OCR 通道扩展）。

覆盖：
- SimHash / 海明距离 / is_similar 算法正确性
- 文本层 PDF：直接取文本块（source=text_layer，不触发 OCR）
- 扫描件 PDF（无文本层）：自动降级本地 OCR 通道（source=ocr，版面由 OCR box 计算）
- 图片文件：OCR 通道提取指纹
- OCR 无结果 / 未启用：指纹标记 empty，不崩溃
"""
from __future__ import annotations

import io
import os
import tempfile

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.services import fingerprint_service as fp_mod
from backend.services.fingerprint_service import FingerprintService
from backend.services.ocr_service import OCRItem, OCRResult

_CN_FONT = r"C:\Windows\Fonts\simhei.ttf"


def _mock_ocr(monkeypatch, items=None, text="", confidence=0.95):
    """替换 ocr_service.recognize_image 为固定结果，避免测试加载真实 OCR 模型。"""
    if items is None:
        items = [
            OCRItem(
                text="销售合同",
                confidence=0.97,
                box=[[72, 80], [140, 80], [140, 96], [72, 96]],
            ),
            OCRItem(
                text="合同号：SJWLXS-2026-YC0453",
                confidence=0.93,
                box=[[72, 120], [280, 120], [280, 136], [72, 136]],
            ),
        ]
    full_text = text if text else "\n".join(it.text for it in items)
    result = OCRResult(text=full_text, confidence=confidence, items=items, provider="fake")

    def fake_recognize(image):
        return result

    monkeypatch.setattr(fp_mod.ocr_service, "recognize_image", fake_recognize)


def _make_text_pdf(texts: list[str]) -> str:
    """有文本层 PDF（中文字体 china-s）。"""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for t in texts:
        page.insert_text((72, y), t, fontsize=12, fontname="china-s")
        y += 30
    doc.save(path)
    doc.close()
    return path


def _make_scanned_pdf(texts: list[str]) -> str:
    """扫描件模拟 PDF：文字渲染成图片嵌入 → 无文本层。"""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    img = Image.new("RGB", (500, 260), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(_CN_FONT, 20)
    y = 20
    for t in texts:
        draw.text((10, y), t, font=font, fill="black")
        y += 36
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 60, 550, 320), stream=buf.getvalue())

    doc.save(path)
    doc.close()
    # 确认无文本层
    check = fitz.open(path)
    assert not check[0].get_text().strip()
    check.close()
    return path


def _make_image_file() -> str:
    """带文字的 JPG 图片。"""
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (500, 200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(_CN_FONT, 20)
    draw.text((10, 30), "发票 税额 开票日期", font=font, fill="black")
    img.save(path, "JPEG")
    return path


# ---------------------------------------------------------------- 算法层

class TestSimHash:
    def test_empty_tokens_all_zero(self):
        assert FingerprintService._compute_simhash([]) == "0" * 16

    def test_same_keywords_close_distance(self):
        h1 = FingerprintService._compute_simhash(["合同", "金额", "船名", "买方"])
        h2 = FingerprintService._compute_simhash(["合同", "金额", "船名", "买方", "卖方"])
        assert FingerprintService.hamming_distance(h1, h2) <= 15

    def test_different_keywords_far_distance(self):
        h1 = FingerprintService._compute_simhash(["合同", "金额", "船名"])
        h2 = FingerprintService._compute_simhash(["发票", "税额", "开票日期"])
        assert FingerprintService.hamming_distance(h1, h2) > 20

    def test_hamming_distance_invalid_input(self):
        assert FingerprintService.hamming_distance("zzz", "abc") == 64
        assert FingerprintService.hamming_distance("", None) == 64

    def test_is_similar_exact_same(self):
        h = FingerprintService._compute_simhash(["合同", "金额"])
        assert FingerprintService.is_similar(h, h) is True


# ---------------------------------------------------------------- 文本层 / OCR 通道

class TestExtractFromPdf:
    def test_text_layer_prefers_no_ocr(self, monkeypatch):
        """有文本层 PDF：source=text_layer，绝不触发 OCR。"""
        def boom(image):
            raise AssertionError("有文本层不应触发 OCR")
        monkeypatch.setattr(fp_mod.ocr_service, "recognize_image", boom)

        path = _make_text_pdf(["销售合同", "合同号：SJWLXS-2026-YC0453", "船名：海丰轮"])
        try:
            layout_hash, simhash, meta = FingerprintService.extract_from_pdf(path)
            assert len(layout_hash) == 64
            assert len(simhash) == 16
            assert meta["source"] == "text_layer"
            assert meta["matched_keywords"]  # 命中了结构关键词
            assert meta["ocr_confidence"] == 1.0
        finally:
            os.remove(path)

    def test_scanned_pdf_uses_ocr_channel(self, monkeypatch):
        """扫描件 PDF：自动降级 OCR 通道，版面由 OCR box 计算。"""
        _mock_ocr(monkeypatch)
        path = _make_scanned_pdf(["销售合同", "船名：海丰轮"])
        try:
            layout_hash, simhash, meta = FingerprintService.extract_from_pdf(path)
            assert len(layout_hash) == 64
            assert len(simhash) == 16
            assert simhash != "0" * 16  # OCR 文本命中了结构关键词
            assert meta["source"] == "ocr"
            assert meta["block_count"] == 2  # mock 返回 2 个文本框
            assert "合同" in meta["matched_keywords"]
            assert meta["ocr_confidence"] == pytest.approx(0.95)
        finally:
            os.remove(path)

    def test_ocr_empty_returns_empty_fingerprint(self, monkeypatch):
        """OCR 无结果：source=empty，SimHash 全 0，不崩溃。"""
        _mock_ocr(monkeypatch, items=[], text="")
        path = _make_scanned_pdf(["纯图片无文字"])
        try:
            layout_hash, simhash, meta = FingerprintService.extract_from_pdf(path)
            assert simhash == "0" * 16
            assert meta["source"] == "empty"
            assert meta["matched_keywords"] == []
        finally:
            os.remove(path)

    def test_same_layout_same_fingerprint(self, monkeypatch):
        """同一版面两次提取指纹一致（OCR 通道）。"""
        _mock_ocr(monkeypatch)
        p1 = _make_scanned_pdf(["销售合同", "合同号：SJWLXS-2026-YC0453"])
        p2 = _make_scanned_pdf(["销售合同", "合同号：SJWLXS-2026-YC0453"])
        try:
            l1, s1, _ = FingerprintService.extract_from_pdf(p1)
            l2, s2, _ = FingerprintService.extract_from_pdf(p2)
            assert l1 == l2
            assert s1 == s2
        finally:
            os.remove(p1)
            os.remove(p2)

    def test_text_layer_and_ocr_same_doc_similar(self, monkeypatch):
        """同一份单据：文本层版与扫描版指纹应相似（结构关键词一致）。"""
        texts = ["销售合同", "合同号：SJWLXS-2026-YC0453", "船名：海丰轮", "买方：宏达"]
        _mock_ocr(monkeypatch)
        p_text = _make_text_pdf(texts)
        p_scan = _make_scanned_pdf(texts)
        try:
            _, s_text, m_text = FingerprintService.extract_from_pdf(p_text)
            _, s_scan, m_scan = FingerprintService.extract_from_pdf(p_scan)
            assert m_text["source"] == "text_layer"
            assert m_scan["source"] == "ocr"
            # 关键词重叠度高 → SimHash 距离小（宽松阈值 15 内）
            assert FingerprintService.hamming_distance(s_text, s_scan) <= 15
        finally:
            os.remove(p_text)
            os.remove(p_scan)


class TestExtractFromImage:
    def test_image_uses_ocr_channel(self, monkeypatch):
        _mock_ocr(monkeypatch)
        path = _make_image_file()
        try:
            layout_hash, simhash, meta = FingerprintService.extract_from_image(path)
            assert len(layout_hash) == 64
            assert len(simhash) == 16
            assert simhash != "0" * 16
            assert meta["source"] == "ocr"
            assert meta["page_count"] == 1
        finally:
            os.remove(path)

    def test_image_ocr_empty(self, monkeypatch):
        _mock_ocr(monkeypatch, items=[], text="")
        path = _make_image_file()
        try:
            _, simhash, meta = FingerprintService.extract_from_image(path)
            assert simhash == "0" * 16
            assert meta["source"] == "empty"
        finally:
            os.remove(path)
