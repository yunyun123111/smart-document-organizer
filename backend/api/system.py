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

from backend.services.dashboard_service import get_dashboard_trends
from backend.services.config_check_service import run_config_checks

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

    # 各类型文件数（看板类型分布，未识别统一归为"未识别"）
    rows = (
        db.query(Document.document_type, func.count(Document.id))
        .group_by(Document.document_type)
        .all()
    )
    type_counts = [{"type": (t or "").strip() or "未识别", "count": c} for t, c in rows]
    type_counts.sort(key=lambda x: -x["count"])

    return {
        "type_counts": type_counts,
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


@router.get("/dashboard/trends")
def dashboard_trends(db: Session = Depends(get_db)):
    """Dashboard 趋势：每日新增/识别成功率/OCR 失败率 + 异常提醒。"""
    return get_dashboard_trends(db)


@router.get("/config-check")
def config_check(db: Session = Depends(get_db)):
    """配置状态校验：目录 / OCR / AI / 邮箱 / 安全 / 阈值 / 数据库。"""
    return run_config_checks(db=db)

