"""批量整理 API：开始 / 进度 / 取消。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ProcessingJob
from backend.schemas.processing import ProcessingJobOut, ProcessingStartRequest
from backend.services.processing_service import processing_service
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/processing", tags=["processing"])
logger = get_logger("api.processing")


@router.post("/start", response_model=ProcessingJobOut)
def start_processing(req: ProcessingStartRequest, db: Session = Depends(get_db)):
    """开始批量整理（扫描 source_dir 或配置的 inbox）。"""
    try:
        job = processing_service.start_job(req.source_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return db.get(ProcessingJob, job.id)


@router.post("/retry-failed", response_model=ProcessingJobOut)
def retry_failed(db: Session = Depends(get_db)):
    """重新处理收件箱中失败过的文件（复用原记录，不重复建档）。"""
    try:
        job = processing_service.retry_failed()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return db.get(ProcessingJob, job.id)


@router.get("/{job_id}", response_model=ProcessingJobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int):
    if not processing_service.cancel_job(job_id):
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return {"ok": True}
