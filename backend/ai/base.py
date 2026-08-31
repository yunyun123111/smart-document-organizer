"""AI Provider 抽象（规格书第四十一节）。

业务代码只调用 AIService，不直接接触具体 Provider / SDK。
更换 AI（OpenAI / DeepSeek / Qwen / Ollama / 本地）时只需新增一个 Provider 实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """AI 提供方抽象。"""

    name: str = "base"

    @abstractmethod
    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        """发送对话消息，返回模型输出文本。"""
        raise NotImplementedError

    def classify_document(self, text: str, context: dict) -> dict[str, Any]:
        """判断文档类型并返回结构化结果（JSON dict）。"""
        raise NotImplementedError

    def extract_fields(self, text: str, document_type: str | None) -> dict[str, Any]:
        """提取字段并返回结构化结果（JSON dict）。"""
        raise NotImplementedError


class FakeAIProvider(AIProvider):
    """测试用假 Provider：返回预设结果，用于单元测试。"""

    name = "fake"

    def __init__(self, result: Any = None, raise_error: bool = False, invalid_json: bool = False):
        self.result = result
        self.raise_error = raise_error
        self.invalid_json = invalid_json
        self.calls = 0
        self.last_text = ""
        self.last_messages: list[dict] = []

    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        self.calls += 1
        self.last_messages = messages
        # 记录发给模型的全部文本
        self.last_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if self.raise_error:
            raise TimeoutError("模拟超时")
        if self.invalid_json:
            return "不是合法JSON"
        return self.result

    def classify_document(self, text: str, context: dict | None = None) -> dict[str, Any]:
        from backend.ai.openai_provider import parse_ai_json

        context = context or {}
        candidate_types = context.get("candidate_types", [])
        msg = f"分类文档。候选类型: {candidate_types}\n{text}"
        return parse_ai_json(self.chat([{"role": "user", "content": msg}]))

    def extract_fields(self, text: str, document_type: str | None) -> dict[str, Any]:
        from backend.ai.openai_provider import parse_ai_json

        msg = f"提取字段。文档类型: {document_type}\n{text}"
        return parse_ai_json(self.chat([{"role": "user", "content": msg}]))
