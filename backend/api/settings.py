"""系统设置 API：读取 / 更新。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.config import settings
from backend.schemas.settings import SettingsOut, SettingsUpdate
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = get_logger("api.settings")


@router.get("", response_model=SettingsOut)
def get_settings():
    return SettingsOut(
        document_root=str(settings.document_root),
        inbox_root=str(settings.inbox_root),
        ocr_enabled=settings.OCR_ENABLED,
        ai_enabled=settings.AI_ENABLED,
        ai_provider=settings.AI_PROVIDER,
        ai_base_url=settings.AI_BASE_URL or "",
        ai_model=settings.AI_MODEL or "",
        ai_api_key_set=bool(settings.AI_API_KEY),
        ai_timeout=settings.AI_TIMEOUT,
        ai_max_retries=settings.AI_MAX_RETRIES,
        ai_max_text_length=settings.AI_MAX_TEXT_LENGTH,
        auto_archive_threshold=settings.AUTO_ARCHIVE_THRESHOLD,
        review_threshold=settings.REVIEW_THRESHOLD,
        conf_rule_weight=settings.CONF_RULE_WEIGHT,
        conf_field_weight=settings.CONF_FIELD_WEIGHT,
        conf_keyword_weight=settings.CONF_KEYWORD_WEIGHT,
        conf_ai_weight=settings.CONF_AI_WEIGHT,
        allow_overwrite=settings.ALLOW_OVERWRITE,
    )


@router.put("", response_model=SettingsOut)
def update_settings(req: SettingsUpdate):
    data = req.model_dump(exclude_unset=True)
    if data:
        settings.update(**data)
        logger.info("更新系统设置: %s", sorted(data.keys()))
    return get_settings()
