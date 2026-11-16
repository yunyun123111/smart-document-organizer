"""Xiaomi-OCR-0 接入测试：OTSL 清洗、引擎工厂、HTTP 调用与失败回退。

全部用假 HTTP 响应，不联网、不加载真实模型。
"""
from __future__ import annotations

import json
import urllib.error

import pytest
from PIL import Image

from backend.config import settings
from backend.services import ocr_service as mod
from backend.services.ocr_service import (
    OCRResult,
    OCRService,
    RapidOCRProvider,
    XiaomiOCRProvider,
    build_provider,
    strip_otsl,
)


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _image() -> Image.Image:
    return Image.new("RGB", (600, 400), "white")


class TestStripOtsl:
    def test_table_row_becomes_pipe_separated(self):
        raw = "<fcel>合同单价<fcel>799.05<fcel>元/吨<nl><fcel>合同数量<fcel>10,000.00<nl>"
        out = strip_otsl(raw)
        lines = out.splitlines()
        assert lines[0] == "合同单价 | 799.05 | 元/吨"
        assert lines[1] == "合同数量 | 10,000.00"

    def test_empty_cells_do_not_leave_trailing_pipes(self):
        raw = "<fcel>From: 华山云创<ecel><ecel><nl>"
        assert strip_otsl(raw) == "From: 华山云创"

    def test_amount_and_invoice_number_preserved(self):
        raw = "<fcel>最终结算金额<fcel>7,922,962.05<fcel>元<nl><fcel>发票号<fcel>24312000000123456789<nl>"
        out = strip_otsl(raw)
        assert "7,922,962.05" in out
        assert "24312000000123456789" in out

    def test_empty_input(self):
        assert strip_otsl("") == ""
        assert strip_otsl(None) == ""


class TestProviderFactory:
    def test_default_is_rapidocr(self, monkeypatch):
        monkeypatch.setattr(settings, "OCR_PROVIDER", "rapidocr")
        assert isinstance(build_provider(), RapidOCRProvider)

    def test_xiaomi_selected(self, monkeypatch):
        monkeypatch.setattr(settings, "OCR_PROVIDER", "xiaomi")
        provider = build_provider()
        assert isinstance(provider, XiaomiOCRProvider)
        assert provider.base_url == settings.OCR_BASE_URL
        assert provider.model == settings.OCR_MODEL

    def test_unknown_value_falls_back(self, monkeypatch):
        monkeypatch.setattr(settings, "OCR_PROVIDER", "not-a-provider")
        assert isinstance(build_provider(), RapidOCRProvider)


class TestXiaomiRecognize:
    def test_payload_and_otsl_parsing(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResp({
                "choices": [{"message": {"content": "<fcel>结算金额<fcel>7,922,962.05<nl>"}}],
                "usage": {"total_tokens": 120},
            })

        monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
        result = XiaomiOCRProvider(base_url="http://127.0.0.1:11434/v1",
                                   model="longwayxu/xiaomi-ocr-0:bf16",
                                   timeout=30).recognize(_image())

        assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
        assert captured["body"]["model"] == "longwayxu/xiaomi-ocr-0:bf16"
        assert captured["body"]["messages"][0]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        assert captured["timeout"] == 30
        assert result.provider == "xiaomi"
        assert "7,922,962.05" in result.text
        assert "<fcel>" not in result.text

    def test_long_edge_is_resized_before_upload(self, monkeypatch):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["len"] = len(req.data)
            return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(settings, "OCR_MAX_IMAGE_EDGE", 320)
        provider = XiaomiOCRProvider()
        result = provider.recognize(Image.new("RGB", (2000, 1000), "white"))
        assert result.text == "ok"
        # 压缩后 320x160 的 PNG 载荷远小于原图
        assert seen["len"] < 200_000

    def test_http_error_raises_runtime_error(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "boom", None,
                __import__("io").BytesIO(b"internal error"),
            )

        monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError) as exc:
            XiaomiOCRProvider().recognize(_image())
        assert "HTTP 500" in str(exc.value)

    def test_broken_response_raises(self, monkeypatch):
        monkeypatch.setattr(
            mod.urllib.request, "urlopen", lambda req, timeout=None: _FakeResp({"error": "x"})
        )
        with pytest.raises(RuntimeError):
            XiaomiOCRProvider().recognize(_image())


class TestServiceFallback:
    def test_xiaomi_failure_falls_back_to_rapidocr(self, monkeypatch):
        class BoomProvider:
            name = "xiaomi"

            def recognize(self, image):
                raise RuntimeError("模型服务未启动")

        monkeypatch.setattr(settings, "OCR_FALLBACK", True)
        monkeypatch.setattr(
            RapidOCRProvider,
            "recognize",
            lambda self, image: OCRResult(text="回退结果 123", confidence=0.9, provider="rapidocr"),
        )
        result = OCRService(provider=BoomProvider()).recognize_image(_image())
        assert result.provider == "rapidocr"
        assert "回退结果" in result.text

    def test_fallback_disabled_reraises(self, monkeypatch):
        class BoomProvider:
            name = "xiaomi"

            def recognize(self, image):
                raise RuntimeError("模型服务未启动")

        monkeypatch.setattr(settings, "OCR_FALLBACK", False)
        with pytest.raises(RuntimeError):
            OCRService(provider=BoomProvider()).recognize_image(_image())

    def test_ocr_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "OCR_ENABLED", False)
        result = OCRService().recognize_image(_image())
        assert result.text == ""
        assert result.provider == "disabled"
