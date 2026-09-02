"""OpenAI Compatible API Provider（规格书第四十一节）。

通过标准 /chat/completions 接口工作，兼容：
OpenAI / DeepSeek / Qwen(通义) / 本地 vLLM / 其他 OpenAI 兼容服务。
使用 httpx 直接调用（不依赖各家 SDK），保持轻量可替换。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from backend.ai.base import AIProvider
from backend.ai.prompts.classify import build_classify_messages
from backend.ai.prompts.extract_fields import build_extract_messages
from backend.utils.logger import get_logger

logger = get_logger("ai.openai_provider")


class OpenAICompatProvider(AIProvider):
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "",
        timeout: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

        self._endpoint = f"{self.base_url}/chat/completions"
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _chat_once(self, messages: list[dict], temperature: float = 0.0) -> str:
        """调用一次 chat completions，返回模型文本。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self._endpoint, json=payload, headers=self._headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        return self._chat_once(messages, temperature)

    def _json_chat(self, messages: list[dict]) -> dict[str, Any]:
        """调用并解析 JSON；非法 JSON 时抛 ValueError（由 AIService 决定重试）。"""
        content = self._chat_once(messages)
        return parse_ai_json(content)

    def classify_document(self, text: str, context: dict) -> dict[str, Any]:
        candidate_types = context.get("candidate_types", [])
        known_fields = context.get("known_fields", {})
        rule_result = context.get("rule_result", "")
        messages = build_classify_messages(text, candidate_types, known_fields, rule_result)
        return self._json_chat(messages)

    def extract_fields(self, text: str, document_type: str | None) -> dict[str, Any]:
        messages = build_extract_messages(text, document_type)
        return self._json_chat(messages)


def parse_ai_json(content: str) -> dict[str, Any]:
    """解析 AI 返回内容为 JSON dict。

    兼容：
    - 纯 JSON
    - 包裹在 ```json ... ``` 代码块中
    - 前后有说明文字
    """
    content = (content or "").strip()
    # 去掉 markdown 代码块
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("AI 返回的不是 JSON 对象")
        return data
    except json.JSONDecodeError:
        # 尝试截取第一个 { 到最后一个 }
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(content[start : end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        raise ValueError("AI 返回非法 JSON")
