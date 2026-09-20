"""系统设置 API：读取 / 更新。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

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
        host_bind=settings.HOST_BIND,
        access_password_set=bool(settings.ACCESS_PASSWORD),
        email_enabled=settings.EMAIL_ENABLED,
        email_imap_host=settings.EMAIL_IMAP_HOST or "",
        email_imap_port=settings.EMAIL_IMAP_PORT,
        email_user=settings.EMAIL_USER or "",
        email_password_set=bool(settings.EMAIL_PASSWORD),
        email_poll_interval=settings.EMAIL_POLL_INTERVAL,
        archive_template=settings.ARCHIVE_TEMPLATE,
    )


@router.get("/archive-templates")
def get_archive_templates():
    """归档模板预设列表（可自由增删，存本地 JSON）。"""
    from backend.services.archive_template_service import list_templates

    return {"templates": list_templates()}


class _ArchiveTemplateItem(BaseModel):
    value: str
    label: str | None = None


@router.post("/archive-templates")
def add_archive_template(item: _ArchiveTemplateItem):
    """新增/更新归档模板预设。"""
    from backend.services.archive_template_service import add_template

    return {"templates": add_template(item.value, item.label)}


@router.delete("/archive-templates")
def delete_archive_template(value: str):
    """删除归档模板预设（按 value 精确匹配；value 含 / 故走 query 参数）。"""
    from backend.services.archive_template_service import remove_template

    return {"templates": remove_template(value)}


@router.put("", response_model=SettingsOut)
def update_settings(req: SettingsUpdate):
    data = req.model_dump(exclude_unset=True)
    if data:
        settings.update(**data)
        logger.info("更新系统设置: %s", sorted(data.keys()))
        # 邮箱配置变更后动态启停轮询线程（无需重启）
        if any(k in data for k in ("email_enabled", "email_imap_host", "email_user", "email_poll_interval")):
            try:
                from backend.services.email_ingest import sync_from_settings

                running = sync_from_settings()
                logger.info("邮箱轮询已同步: running=%s", running)
            except Exception:
                logger.exception("邮箱轮询同步失败")
    return get_settings()
