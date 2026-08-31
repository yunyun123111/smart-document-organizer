"""系统设置 Pydantic 模型。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SettingsOut(BaseModel):
    """可查看/修改的系统设置（不含敏感 key 明文展示时的 API Key）。"""
    document_root: str
    inbox_root: str
    ocr_enabled: bool
    ai_enabled: bool
    ai_provider: str
    ai_base_url: str
    ai_model: str
    ai_api_key_set: bool  # 是否已配置
    ai_timeout: int
    ai_max_retries: int
    ai_max_text_length: int
    auto_archive_threshold: float
    review_threshold: float
    conf_rule_weight: float
    conf_field_weight: float
    conf_keyword_weight: float
    conf_ai_weight: float
    allow_overwrite: bool


class SettingsUpdate(BaseModel):
    """允许更新的设置字段（均可选）。"""
    document_root: Optional[str] = None
    inbox_root: Optional[str] = None
    ocr_enabled: Optional[bool] = None
    ai_enabled: Optional[bool] = None
    ai_provider: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None
    ai_timeout: Optional[int] = None
    ai_max_retries: Optional[int] = None
    ai_max_text_length: Optional[int] = None
    auto_archive_threshold: Optional[float] = None
    review_threshold: Optional[float] = None
    conf_rule_weight: Optional[float] = None
    conf_field_weight: Optional[float] = None
    conf_keyword_weight: Optional[float] = None
    conf_ai_weight: Optional[float] = None
    allow_overwrite: Optional[bool] = None
