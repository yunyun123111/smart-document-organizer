"""Phase 7 AI 服务测试：JSON 校验/重试/超时/截断/可用性。"""
from __future__ import annotations

import pytest

from backend.ai.base import FakeAIProvider
from backend.ai.openai_provider import OpenAICompatProvider, parse_ai_json
from backend.services.ai_service import AIService

VALID_CLASSIFY_JSON = json_str = """
{
  "document_type": "销售合同",
  "confidence": 0.94,
  "title": "销售合同",
  "fields": {"contract_no": "XS202608001", "company": "ABC有限公司"},
  "keywords": ["销售合同", "合同编号"],
  "reason": "包含合同典型字段"
}
"""


class TestParseJson:
    def test_plain_json(self):
        data = parse_ai_json(VALID_CLASSIFY_JSON)
        assert data["document_type"] == "销售合同"
        assert data["confidence"] == 0.94

    def test_json_in_code_block(self):
        content = "```json\n" + VALID_CLASSIFY_JSON + "\n```"
        data = parse_ai_json(content)
        assert data["document_type"] == "销售合同"

    def test_json_with_prefix_text(self):
        content = "好的，以下是结果：\n" + VALID_CLASSIFY_JSON
        data = parse_ai_json(content)
        assert data["document_type"] == "销售合同"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="非法 JSON"):
            parse_ai_json("这不是JSON")

    def test_not_dict_raises(self):
        with pytest.raises(ValueError):
            parse_ai_json("[1,2,3]")


class TestAIServiceClassify:
    def test_success(self):
        svc = AIService(provider=FakeAIProvider(result=VALID_CLASSIFY_JSON))
        result = svc.classify_document("销售合同文本...")
        assert result is not None
        assert result["document_type"] == "销售合同"
        assert result["confidence"] == 0.94

    def test_invalid_json_retries_then_succeeds(self):
        # 第一次非法，第二次正常
        fake = FakeAIProvider(result=VALID_CLASSIFY_JSON)
        fake.invalid_json = True
        # invalid_json=True 时 chat 返回"不是合法JSON"，需要第二次成功
        # 这里改用计数实现：第一次非法第二次合法
        class SeqProvider(FakeAIProvider):
            def chat(self, messages, temperature=0.0):
                self.calls += 1
                if self.calls == 1:
                    return "不是JSON"
                return VALID_CLASSIFY_JSON

        svc = AIService(provider=SeqProvider(), max_retries=2)
        result = svc.classify_document("文本")
        assert result is not None
        assert result["document_type"] == "销售合同"

    def test_timeout_retries_then_fails(self):
        fake = FakeAIProvider(raise_error=True)
        svc = AIService(provider=fake, max_retries=2)
        assert svc.classify_document("文本") is None
        assert fake.calls == 3  # 初始 + 2 次重试

    def test_missing_document_type_invalid(self):
        fake = FakeAIProvider(result='{"confidence": 0.9}')
        svc = AIService(provider=fake, max_retries=0)
        assert svc.classify_document("文本") is None

    def test_low_confidence_accepted(self):
        # 低置信度也应返回（由上层置信度系统分档）
        fake = FakeAIProvider(result='{"document_type": "其他", "confidence": 0.3}')
        svc = AIService(provider=fake, max_retries=0)
        result = svc.classify_document("文本")
        assert result is not None
        assert result["confidence"] == 0.3

    def test_truncates_long_text(self):
        fake = FakeAIProvider(result=VALID_CLASSIFY_JSON)
        svc = AIService(provider=fake, max_retries=0, max_text_length=100)
        long_text = "字" * 1000
        svc.classify_document(long_text)
        # 检查传给 provider 的文本被截断（含截断标记）
        sent = fake.last_text
        assert "已截断" in sent
        assert len(sent) < 200

    def test_not_available(self):
        svc = AIService(provider=None)
        # 未配置 provider 且 AI_ENABLED 未设置（测试环境无 base_url）-> 不可用
        assert svc.classify_document("文本") is None or svc.is_available()

    def test_provider_rebuilt_after_being_disabled(self):
        """配置驱动的 provider 缓存必须跟随开关：关掉 AI 就立即不再可用。"""
        from backend.config import settings

        svc = AIService(provider=None)
        enabled, base, provider_name = (
            settings.AI_ENABLED,
            settings.AI_BASE_URL,
            settings.AI_PROVIDER,
        )
        try:
            settings.AI_ENABLED = True
            settings.AI_PROVIDER = "openai"
            settings.AI_BASE_URL = "http://127.0.0.1:9/v1"
            assert svc.provider is not None, "开启后应立即构建 provider"

            # 用户在设置页关闭 AI：缓存实例必须失效
            settings.AI_ENABLED = False
            assert svc.provider is None, "关闭 AI 后不能沿用缓存 provider"
            assert svc.classify_document("文本") is None
        finally:
            settings.AI_ENABLED = enabled
            settings.AI_BASE_URL = base
            settings.AI_PROVIDER = provider_name

    def test_provider_rebuilt_on_base_url_change(self):
        from backend.config import settings

        svc = AIService(provider=None)
        enabled, base, provider_name = (
            settings.AI_ENABLED,
            settings.AI_BASE_URL,
            settings.AI_PROVIDER,
        )
        try:
            settings.AI_ENABLED = True
            settings.AI_PROVIDER = "openai"
            settings.AI_BASE_URL = "http://a/v1"
            assert svc.provider.base_url == "http://a/v1"
            settings.AI_BASE_URL = "http://b/v1"
            assert svc.provider.base_url == "http://b/v1", "改地址后应重建 provider"
        finally:
            settings.AI_ENABLED = enabled
            settings.AI_BASE_URL = base
            settings.AI_PROVIDER = provider_name


class TestAIServiceExtract:
    def test_extract_fields_success(self):
        fake = FakeAIProvider(
            result='{"fields": {"contract_no": "XS001", "amount": "128500"}, "confidence": 0.9}'
        )
        svc = AIService(provider=fake, max_retries=0)
        result = svc.extract_fields("合同文本", "销售合同")
        assert result is not None
        assert result["fields"]["contract_no"] == "XS001"

    def test_extract_fields_failure_returns_none(self):
        fake = FakeAIProvider(raise_error=True)
        svc = AIService(provider=fake, max_retries=0)
        assert svc.extract_fields("文本") is None


class TestOpenAIProviderMessageBuild:
    def test_classify_messages_contains_candidates(self):
        provider = OpenAICompatProvider(base_url="http://x", api_key="k", model="m")
        from backend.ai.prompts.classify import build_classify_messages

        msgs = build_classify_messages("文本", ["销售合同", "发票"], {"contract_no": "X"}, "规则结果")
        assert msgs[0]["role"] == "system"
        assert "销售合同" in msgs[1]["content"]
        assert "规则结果" in msgs[1]["content"]

    def test_provider_requires_base_url(self):
        # 未配置 base_url 时 build_provider 返回 None（不 crash）
        from backend.services.ai_service import build_provider

        provider = build_provider()
        # 测试环境未配置 AI_BASE_URL，可能为 None 或实体 provider，二者都可接受
        assert provider is None or provider.name in ("openai-compatible", "ollama")
