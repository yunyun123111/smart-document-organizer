"""AI 服务统一入口（规格书第二十五至二十七节）。

- 业务代码只调用 AIService，不直接接触 Provider
- 统一 JSON 校验、错误重试、超时处理
- 长文本限制长度（AI 安全：不整篇发送超大文件）
- AI 不可用/失败时返回 None，由上层降级到规则或人工审核
"""
from __future__ import annotations

import json
from typing import Any, Callable

from backend.ai.base import AIProvider
from backend.ai.openai_provider import OpenAICompatProvider, parse_ai_json
from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger("services.ai_service")


def build_provider() -> AIProvider | None:
    """根据配置构建 AI Provider。未配置时返回 None。"""
    if not settings.AI_ENABLED:
        return None
    provider_name = (settings.AI_PROVIDER or "").lower()

    if provider_name == "ollama":
        from backend.ai.ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=settings.AI_BASE_URL or OllamaProvider.DEFAULT_OLLAMA_URL,
            model=settings.AI_MODEL or "qwen2.5",
            timeout=settings.AI_TIMEOUT,
        )

    # openai / deepseek / qwen / custom / 其他 -> OpenAI Compatible
    if not settings.AI_BASE_URL:
        logger.warning("AI 未配置 base_url，AI 服务不可用")
        return None
    return OpenAICompatProvider(
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL or "default",
        timeout=settings.AI_TIMEOUT,
    )


class AIService:
    """AI 服务：分类与字段提取，含校验/重试/截断/熔断。"""

    # 熔断参数：连续失败 N 次后，暂停调用 N 秒（防止配错模型时拖垮批量）
    CIRCUIT_BREAK_FAILURES = 3
    CIRCUIT_BREAK_COOLDOWN = 60

    def __init__(
        self,
        provider: AIProvider | None = None,
        max_retries: int | None = None,
        max_text_length: int | None = None,
    ):
        self._provider = provider
        self._provider_key: tuple | None = None  # 构建 provider 时的配置指纹
        self.max_retries = max_retries if max_retries is not None else settings.AI_MAX_RETRIES
        self.max_text_length = (
            max_text_length if max_text_length is not None else settings.AI_MAX_TEXT_LENGTH
        )
        # 熔断状态
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    @staticmethod
    def _config_key() -> tuple:
        """AI 相关配置指纹：任一项变化都要重建 Provider。"""
        return (
            settings.AI_ENABLED,
            (settings.AI_PROVIDER or "").lower(),
            settings.AI_BASE_URL,
            settings.AI_API_KEY,
            settings.AI_MODEL,
            settings.AI_TIMEOUT,
        )

    @property
    def provider(self) -> AIProvider | None:
        key = self._config_key()
        # 外部显式注入的 provider（如测试）不受配置指纹影响
        if self._provider is not None and self._provider_key is None:
            return self._provider
        if self._provider is None or self._provider_key != key:
            self._provider = build_provider()
            self._provider_key = key
            # 配置变了（换模型/改地址）应重新给一次机会，不延续旧熔断
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
        return self._provider

    def is_available(self) -> bool:
        if self._circuit_open():
            return False
        return self.provider is not None

    def _circuit_open(self) -> bool:
        """熔断是否开启（开启期间直接跳过 AI）。"""
        import time

        if self._consecutive_failures >= self.CIRCUIT_BREAK_FAILURES:
            if time.monotonic() >= self._circuit_open_until:
                # 冷却结束，允许重试一次
                self._consecutive_failures = 0
                return False
            return True
        return False

    def _record_failure(self) -> None:
        import time

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.CIRCUIT_BREAK_FAILURES:
            self._circuit_open_until = time.monotonic() + self.CIRCUIT_BREAK_COOLDOWN
            logger.warning(
                "AI 连续失败 %d 次，进入熔断 %d 秒（本轮批量将跳过 AI，使用纯规则）",
                self._consecutive_failures,
                self.CIRCUIT_BREAK_COOLDOWN,
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _truncate_text(self, text: str) -> str:
        """限制文本长度（AI 安全）。过长时保留开头与结尾。"""
        text = text or ""
        if len(text) <= self.max_text_length:
            return text
        keep = self.max_text_length // 2
        return text[:keep] + "\n…[已截断]…\n" + text[-keep:]

    def _call_json(
        self,
        fn: Callable[[], dict[str, Any]],
        validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """带重试的 JSON 调用。超时/非法 JSON/API 错误时重试，仍失败返回 None。"""
        last_err: Exception | None = None
        validate = validator or self._validate_classify
        for attempt in range(self.max_retries + 1):
            try:
                result = fn()
                validate(result)
                self._record_success()
                return result
            except (TimeoutError, ConnectionError) as e:
                last_err = e
                logger.warning("AI 调用超时/连接失败（第 %d 次）: %s", attempt + 1, e)
            except (ValueError, KeyError, TypeError) as e:
                last_err = e
                logger.warning("AI 返回校验失败（第 %d 次）: %s", attempt + 1, e)
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("AI 调用异常（第 %d 次）: %s", attempt + 1, e)
        self._record_failure()
        logger.error("AI 调用最终失败: %s", last_err)
        return None

    def _validate_classify(self, data: dict[str, Any]) -> None:
        """验证分类结果的关键字段。"""
        if "document_type" not in data or not data.get("document_type"):
            raise ValueError("AI 结果缺少 document_type")
        conf = data.get("confidence")
        if not isinstance(conf, (int, float)) or not (0 <= float(conf) <= 1):
            raise ValueError(f"AI 结果 confidence 非法: {conf}")

    @staticmethod
    def _validate_extract(data: dict[str, Any]) -> None:
        """验证字段提取结果：必须包含 fields 字典。"""
        fields = data.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("AI 结果缺少 fields 对象")

    def classify_document(
        self, text: str, context: dict | None = None
    ) -> dict[str, Any] | None:
        """判断文档类型。失败/不可用返回 None。"""
        if not self.is_available():
            logger.info("AI 未启用，跳过分类")
            return None
        provider = self.provider
        assert provider is not None
        text = self._truncate_text(text)
        context = context or {}

        def _do():
            return provider.classify_document(text, context)

        return self._call_json(_do)

    def extract_fields(
        self, text: str, document_type: str | None = None
    ) -> dict[str, Any] | None:
        """提取字段。失败/不可用返回 None。"""
        if not self.is_available():
            return None
        provider = self.provider
        assert provider is not None
        text = self._truncate_text(text)

        def _do():
            return provider.extract_fields(text, document_type)

        return self._call_json(_do, validator=self._validate_extract)


# 全局单例
ai_service = AIService()
