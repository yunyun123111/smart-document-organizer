"""应用配置系统。

使用 pydantic-settings 从 .env / 环境变量加载配置。
所有路径统一解析为绝对路径，避免工作目录变化导致的问题。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 = 本文件的上两级目录（backend/config.py -> 项目根）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用基础
    APP_NAME: str = "SmartDocumentOrganizer"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # 数据库
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data/database/app.db'}"

    # 目录
    DOCUMENT_ROOT: str = str(BASE_DIR / "data/documents")
    INBOX_ROOT: str = str(BASE_DIR / "data/inbox")
    TEMP_DIR: str = str(BASE_DIR / "data/temp")

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = str(BASE_DIR / "data/logs")

    # 文件操作
    ALLOW_OVERWRITE: bool = False

    # OCR（P4 阶段接入 PaddleOCR）
    OCR_ENABLED: bool = True

    # AI（OpenAI Compatible API）
    AI_ENABLED: bool = True
    AI_PROVIDER: str = "openai"  # openai | deepseek | qwen | ollama | custom
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = ""
    AI_TIMEOUT: int = 60
    AI_MAX_RETRIES: int = 2
    AI_MAX_TEXT_LENGTH: int = 1500

    # 置信度阈值
    AUTO_ARCHIVE_THRESHOLD: float = 0.85
    REVIEW_THRESHOLD: float = 0.60

    # 置信度权重
    CONF_RULE_WEIGHT: float = 0.40
    CONF_FIELD_WEIGHT: float = 0.20
    CONF_KEYWORD_WEIGHT: float = 0.15
    CONF_AI_WEIGHT: float = 0.25

    # ---- 派生属性 ----
    @property
    def document_root(self) -> Path:
        return Path(self.DOCUMENT_ROOT)

    @property
    def inbox_root(self) -> Path:
        return Path(self.INBOX_ROOT)

    @property
    def temp_dir(self) -> Path:
        return Path(self.TEMP_DIR)

    @property
    def log_dir(self) -> Path:
        return Path(self.LOG_DIR)

    def ensure_dirs(self) -> None:
        """确保所有运行所需目录存在。"""
        for p in (
            self.document_root,
            self.inbox_root,
            self.temp_dir,
            self.log_dir,
            Path(self.DATABASE_URL.replace("sqlite:///", "")).parent
            if self.DATABASE_URL.startswith("sqlite")
            else BASE_DIR / "data/database",
        ):
            p.mkdir(parents=True, exist_ok=True)

    def confidence_weights(self) -> dict[str, float]:
        """返回置信度权重字典（规则/字段/关键词/AI）。"""
        return {
            "rule": self.CONF_RULE_WEIGHT,
            "field": self.CONF_FIELD_WEIGHT,
            "keyword": self.CONF_KEYWORD_WEIGHT,
            "ai": self.CONF_AI_WEIGHT,
        }

    # 供 update() 使用的小写别名 -> 真实配置字段名
    _FIELD_ALIASES = {
        "document_root": "DOCUMENT_ROOT",
        "inbox_root": "INBOX_ROOT",
        "temp_dir": "TEMP_DIR",
        "log_dir": "LOG_DIR",
    }

    def _resolve_field(self, key: str) -> str:
        """把调用方传入的字段名解析为模型真实字段名（支持小写别名）。"""
        if key in self._FIELD_ALIASES:
            return self._FIELD_ALIASES[key]
        for field in type(self).model_fields:
            if field == key or field.lower() == key.lower():
                return field
        return key

    def update(self, **kwargs) -> None:
        """更新内存配置并持久化到 .env（None 值跳过）。

        只持久化本次显式传入的字段，避免把环境变量/测试注入值泄漏到 .env。
        """
        changed: list[str] = []
        for key, value in kwargs.items():
            if value is None:
                continue
            field = self._resolve_field(key)
            if hasattr(self, field) and getattr(self, field) != value:
                setattr(self, field, value)
                changed.append(field)
        if changed:
            self.ensure_dirs()
            self._persist(changed)

    def _persist(self, fields: list[str] | None = None) -> None:
        """把指定字段写回 .env 文件（保留已有注释与未管理项）。"""
        env_path = BASE_DIR / ".env"
        data = {f: getattr(self, f) for f in fields} if fields else self.model_dump()
        lines: list[str] = []
        written = set()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key = line.split("=", 1)[0].strip()
                    if key in data:
                        lines.append(f"{key}={data[key]}")
                        written.add(key)
                        continue
                lines.append(line)
        for key, value in data.items():
            if key not in written:
                lines.append(f"{key}={value}")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logging.getLogger("config").info("配置已持久化到 %s", env_path)


settings = Settings()
