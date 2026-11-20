"""系统 API：健康检查、基础信息、Dashboard 统计。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import (
    CANDIDATE_PENDING,
    DECISION_APPROVE,
    DECISION_SKIP,
    JOB_RUNNING,
    STATUS_ARCHIVED,
    STATUS_NEED_REVIEW,
    STATUS_RECYCLED,
    Document,
    DocumentSample,
    ProcessingJob,
    ReviewDecision,
    RuleCandidate,
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
    # 统计排除回收站文件
    total_documents = (
        db.query(func.count(Document.id))
        .filter(Document.status != STATUS_RECYCLED)
        .scalar()
        or 0
    )
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
        .filter(
            func.date(Document.created_at) == func.date("now", "localtime"),
            Document.status != STATUS_RECYCLED,
        )
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
        .filter(Document.status != STATUS_RECYCLED)
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


@router.get("/dashboard/review-quality")
def dashboard_review_quality(db: Session = Depends(get_db)):
    """识别质量看板：人工改判率、学习成果、置信度分布、积压。

    数据源是 review_decisions（每次确认/跳过都留痕），不做任何推断。
    改判率 = 人工改了 AI 分类的确认次数 / 确认总次数。
    """
    total_decisions = db.query(func.count(ReviewDecision.id)).scalar() or 0
    approved = (
        db.query(func.count(ReviewDecision.id))
        .filter(ReviewDecision.action == DECISION_APPROVE)
        .scalar()
        or 0
    )
    skipped = (
        db.query(func.count(ReviewDecision.id))
        .filter(ReviewDecision.action == DECISION_SKIP)
        .scalar()
        or 0
    )
    category_changed = (
        db.query(func.count(ReviewDecision.id))
        .filter(ReviewDecision.category_changed.is_(True))
        .scalar()
        or 0
    )
    type_changed = (
        db.query(func.count(ReviewDecision.id))
        .filter(ReviewDecision.type_changed.is_(True))
        .scalar()
        or 0
    )

    samples_total = db.query(func.count(DocumentSample.id)).scalar() or 0
    samples_auto = (
        db.query(func.count(DocumentSample.id))
        .filter(DocumentSample.note.like("由审核确认自动学习%"))
        .scalar()
        or 0
    )
    pending_candidates = (
        db.query(func.count(RuleCandidate.id))
        .filter(RuleCandidate.status == CANDIDATE_PENDING)
        .scalar()
        or 0
    )
    pending_review = (
        db.query(func.count(Document.id))
        .filter(Document.status == STATUS_NEED_REVIEW)
        .scalar()
        or 0
    )

    # 置信度分桶：低置信的单据最该被改判，这里的比例能说明阈值是否偏松
    bucket_specs = [
        (0.0, 0.5, "低 (<0.5)"),
        (0.5, 0.7, "中低 (0.5~0.7)"),
        (0.7, 0.85, "中高 (0.7~0.85)"),
        (0.85, 1.01, "高 (≥0.85)"),
    ]
    buckets = []
    for lo, hi, label in bucket_specs:
        cnt = (
            db.query(func.count(ReviewDecision.id))
            .filter(
                ReviewDecision.confidence.isnot(None),
                ReviewDecision.confidence >= lo,
                ReviewDecision.confidence < hi,
            )
            .scalar()
            or 0
        )
        changed = (
            db.query(func.count(ReviewDecision.id))
            .filter(
                ReviewDecision.confidence.isnot(None),
                ReviewDecision.confidence >= lo,
                ReviewDecision.confidence < hi,
                ReviewDecision.category_changed.is_(True),
            )
            .scalar()
            or 0
        )
        buckets.append({"label": label, "count": cnt, "changed": changed})

    rate = round(category_changed / approved * 100, 1) if approved else 0.0
    return {
        "total_decisions": total_decisions,
        "approved": approved,
        "skipped": skipped,
        "category_changed": category_changed,
        "type_changed": type_changed,
        "category_change_rate": rate,
        "samples_total": samples_total,
        "samples_auto": samples_auto,
        "pending_candidates": pending_candidates,
        "pending_review": pending_review,
        "confidence_buckets": buckets,
    }


@router.get("/dashboard/trends")
def dashboard_trends(days: int = Query(14, ge=7, le=30), db: Session = Depends(get_db)):
    """Dashboard 趋势：每日新增/识别成功率/OCR 失败率 + 异常提醒。

    days: 时间范围（7/14/30 天），前端趋势图切换用。
    """
    return get_dashboard_trends(db, days=days)


@router.get("/config-check")
def config_check(db: Session = Depends(get_db)):
    """配置状态校验：目录 / OCR / AI / 邮箱 / 安全 / 阈值 / 数据库。"""
    return run_config_checks(db=db)


@router.get("/watch-status")
def watch_status():
    """目录自动监控状态（运行/目录/最近扫描/触发任务）。"""
    from backend.services.folder_watcher import get_status

    return get_status()

