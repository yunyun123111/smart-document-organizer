"""视觉 KIE 兜底测试：JSON 解析容错、值清洗、prompt、HTTP 调用、触发判定与字段合并。

全部用假 HTTP 与内存库，不联网。
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest
from PIL import Image

from backend.config import settings
from backend.models.recognition_template import RecognitionTemplate
from backend.services import ocr_kie_service as mod
from backend.services.classifier import AnalysisResult, ClassifierService
from backend.services.ocr_kie_service import (
    KIEResult,
    OCRKIEService,
    _normalize,
    parse_json_object,
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
    return Image.new("RGB", (400, 300), "white")


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class TestParseJson:
    def test_plain_json(self):
        assert parse_json_object('{"amount": "100"}') == {"amount": "100"}

    def test_fenced_json(self):
        raw = '```json\n{"amount": "1,000.00"}\n```'
        assert parse_json_object(raw) == {"amount": "1,000.00"}

    def test_noise_around_json(self):
        raw = '好的，结果如下：{"company": "宏达"} 以上。'
        assert parse_json_object(raw) == {"company": "宏达"}

    def test_invalid_returns_empty(self):
        assert parse_json_object("这次识别失败了") == {}
        assert parse_json_object("") == {}

    def test_non_object_json_returns_empty(self):
        assert parse_json_object("[1, 2]") == {}


class TestNormalize:
    def test_amount_strips_thousands_and_unit(self):
        assert _normalize("amount", "7,922,962.05 元") == "7,922,962.05"

    def test_quantity_strips_unit(self):
        assert _normalize("quantity", "9,914.99 吨") == "9,914.99"

    def test_date_to_iso(self):
        assert _normalize("date", "2026/6/25") == "2026-06-25"
        assert _normalize("date", "2026年6月25日") == "2026-06-25"

    def test_null_and_placeholders_dropped(self):
        for v in (None, "null", "无", "", "N/A"):
            assert _normalize("company", v) == ""

    def test_contract_no_kept_verbatim(self):
        raw = "HSYCXS (DD)-2026-0236"
        assert _normalize("contract_no", raw) == raw


class TestPrompt:
    def test_prompt_lists_default_fields(self):
        p = OCRKIEService().build_prompt()
        for key in ("amount", "contract_no", "invoice_no", "date"):
            assert key in p
        assert "只输出一个 JSON 对象" in p

    def test_all_fields_requested_by_default(self):
        """默认给全字段：收窄字段会丢表格语境，把单价当结算金额。"""
        p = OCRKIEService().build_prompt()
        assert "- quantity:" in p
        assert "- material:" in p
        assert "不要取合同单价" in p

    def test_doc_type_included(self):
        p = OCRKIEService().build_prompt(document_type="结算单")
        assert "单据类型：结算单" in p


class TestExtract:
    def test_parses_fields_and_normalizes(self, monkeypatch):
        content = (
            '{"company": "宏达", "amount": "7,922,962.05", '
            '"contract_no": "HSYCXS (DD)-2026-0236", "date": "2026/6/25", "vessel": null}'
        )
        monkeypatch.setattr(
            mod.urllib.request, "urlopen",
            lambda *a, **k: _FakeResp(_chat_payload(content)),
        )
        res = OCRKIEService(base_url="http://x/v1", model="m").extract(_image())
        assert res.ok
        assert res.fields["amount"] == "7,922,962.05"
        assert res.fields["date"] == "2026-06-25"
        assert "vessel" not in res.fields
        assert res.elapsed >= 0

    def test_unknown_field_ignored(self, monkeypatch):
        content = '{"amount": "100", "prefix": "x"}'
        monkeypatch.setattr(
            mod.urllib.request, "urlopen",
            lambda *a, **k: _FakeResp(_chat_payload(content)),
        )
        res = OCRKIEService(base_url="http://x/v1").extract(_image())
        assert res.fields == {"amount": "100"}

    def test_http_error_returns_error_without_raising(self, monkeypatch):
        def boom(*a, **k):
            raise urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"server down"))

        monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
        res = OCRKIEService(base_url="http://x/v1").extract(_image())
        assert not res.ok
        assert "HTTP 500" in res.error

    def test_network_error_returns_error(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
        res = OCRKIEService(base_url="http://x/v1").extract(_image())
        assert not res.ok
        assert "connection refused" in res.error

    def test_reset_clears_explicit_params(self):
        svc = OCRKIEService(base_url="http://tmp", model="tmpm", timeout=9)
        svc.reset()
        assert svc.base_url == settings.OCR_BASE_URL.rstrip("/")
        assert svc.model == settings.OCR_MODEL
        assert svc.timeout == settings.OCR_TIMEOUT


class TestTriggerFields:
    def test_missing_amount_triggers(self, db):
        svc = ClassifierService(db)
        assert svc._kie_trigger_fields(None, {}, 0.9) == ["amount"]

    def test_amount_present_and_confident_does_not_trigger(self, db):
        svc = ClassifierService(db)
        assert svc._kie_trigger_fields(None, {"amount": "100"}, 0.9) == []

    def test_low_field_conf_expands_fields(self, db):
        monkeypatch_conf = 0.4
        svc = ClassifierService(db)
        out = set(svc._kie_trigger_fields(None, {}, monkeypatch_conf))
        assert {"amount", "contract_no", "invoice_no", "date", "company"} <= out

    def test_template_required_fields_driven(self, db):
        db.add(RecognitionTemplate(
            document_type="结算单",
            category_path="结算/结算单",
            require_fields=json.dumps(["date", "vessel"], ensure_ascii=False),
            enabled=True,
        ))
        db.commit()

        class _Best:
            category_name = "结算单"

        svc = ClassifierService(db)
        # date/vessel 都齐 -> 不触发
        assert svc._kie_trigger_fields(_Best(), {"date": "2026-06-25", "vessel": "X"}, 0.9) == []
        # 缺 vessel -> 只补 vessel
        assert svc._kie_trigger_fields(_Best(), {"date": "2026-06-25"}, 0.9) == ["vessel"]


class TestMergeFields:
    def _result(self, values: dict) -> AnalysisResult:
        r = AnalysisResult(file_path="x.jpg")
        r.field_values = dict(values)
        r.fields = {k: (v, 0.7, "RULE") for k, v in values.items()}
        return r

    def test_fills_missing_with_kie_conf(self, db):
        svc = ClassifierService(db)
        r = self._result({"company": "宏达"})
        svc._merge_kie_fields(r, {"amount": "7,922,962.05"}, ["amount"])
        assert r.field_values["amount"] == "7,922,962.05"
        assert r.fields["amount"][1] == settings.OCR_KIE_CONF
        assert r.fields["amount"][2] == "KIE"

    def test_amount_conflict_overrides_and_records(self, db):
        svc = ClassifierService(db)
        r = self._result({"amount": "7,990,545.00"})
        svc._merge_kie_fields(r, {"amount": "7,922,962.05"}, ["amount"])
        assert r.field_values["amount"] == "7,922,962.05"
        assert r.field_conflicts[0]["chosen"] == "7,922,962.05"
        assert "覆盖" in r.field_conflicts[0]["reason"]

    def test_company_conflict_keeps_existing(self, db):
        svc = ClassifierService(db)
        r = self._result({"company": "华山云创"})
        svc._merge_kie_fields(r, {"company": "宏达矿业"}, ["company"])
        assert r.field_values["company"] == "华山云创"
        assert "保留" in r.field_conflicts[0]["reason"]

    def test_fields_outside_trigger_list_ignored(self, db):
        svc = ClassifierService(db)
        r = self._result({})
        svc._merge_kie_fields(r, {"vessel": "远洋1号"}, ["amount"])
        assert "vessel" not in r.field_values

    def test_same_value_no_conflict(self, db):
        svc = ClassifierService(db)
        r = self._result({"amount": "100"})
        svc._merge_kie_fields(r, {"amount": "100"}, ["amount"])
        assert r.field_conflicts == []

    def test_contract_no_space_difference_keeps_original(self, db):
        """模型常丢合同号里的空格，仅空格/大小写差异不覆盖原值。"""
        svc = ClassifierService(db)
        r = self._result({"contract_no": "HSYCXS (DD)-2026-0236"})
        svc._merge_kie_fields(r, {"contract_no": "HSYCXS(DD)-2026-0236"}, ["contract_no"])
        assert r.field_values["contract_no"] == "HSYCXS (DD)-2026-0236"
        assert r.field_conflicts == []
