"""Ollama Provider。

Ollama 提供 OpenAI 兼容接口（/v1/chat/completions），因此复用 OpenAI 协议，
仅默认 base_url 指向本地 Ollama。
"""
from __future__ import annotations

from backend.ai.openai_provider import OpenAICompatProvider

DEFAULT_OLLAMA_URL = "http://localhost:11434/v1"


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        api_key: str = "ollama",
        model: str = "qwen2.5",
        timeout: int = 120,
    ):
        super().__init__(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
