"""系统 API：健康检查、基础信息、Dashboard 统计。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import (
    JOB_RUNNING,
    STATUS_ARCHIVED,
    STATUS_NEED_REVIEW,
    Document,
    ProcessingJob,
)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    """健康检查：确认后端与数据库正常。"""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "environment": settings.APP_ENV,
    }


@router.get("/info")
def info():
    """系统基础信息（不含敏感配置）。"""
    return {
        "app": settings.APP_NAME,
        "document_root": settings.DOCUMENT_ROOT,
        "inbox_root": settings.INBOX_ROOT,
        "ocr_enabled": settings.OCR_ENABLED,
        "ai_enabled": settings.AI_ENABLED,
        "ai_provider": settings.AI_PROVIDER,
        "auto_archive_threshold": settings.AUTO_ARCHIVE_THRESHOLD,
        "review_threshold": settings.REVIEW_THRESHOLD,
    }


@router.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    """Dashboard 统计：总文档/已归档/待审核/今日处理/运行中任务。"""
    total_documents = db.query(func.count(Document.id)).scalar() or 0
    total_archived = (
        db.query(func.count(Document.id)).filter(Document.status == STATUS_ARCHIVED).scalar() or 0
    )
    pending_review = (
        db.query(func.count(Document.id)).filter(Document.status == STATUS_NEED_REVIEW).scalar() or 0
    )
    running_job = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.status == JOB_RUNNING)
        .order_by(ProcessingJob.id.desc())
        .first()
    )
    today_total = (
        db.query(func.count(Document.id))
        .filter(func.date(Document.created_at) == func.date("now", "localtime"))
        .scalar()
        or 0
    )
    today_archived = (
        db.query(func.count(Document.id))
        .filter(
            Document.status == STATUS_ARCHIVED,
            func.date(Document.created_at) == func.date("now", "localtime"),
        )
        .scalar()
        or 0
    )

    return {
        "total_documents": total_documents,
        "total_archived": total_archived,
        "pending_review": pending_review,
        "today_total": today_total,
        "today_archived": today_archived,
        "running_job": running_job.id if running_job else None,
        "document_root": str(settings.DOCUMENT_ROOT),
        "inbox_root": str(settings.INBOX_ROOT),
        "ai_enabled": settings.AI_ENABLED and bool(settings.AI_BASE_URL),
        "ocr_enabled": settings.OCR_ENABLED,
    }
