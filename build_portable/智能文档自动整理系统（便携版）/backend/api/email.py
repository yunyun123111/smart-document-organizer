"""邮箱接收 API：状态查询 + 立即检查。"""
from __future__ import annotations

from fastapi import APIRouter

from backend.services import email_ingest
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/email", tags=["email"])
logger = get_logger("api.email")


@router.get("/status")
def email_status():
    """返回邮箱接收状态：是否启用/轮询运行中/最近一次收取结果与错误。"""
    return email_ingest.get_status()


@router.post("/check")
def email_check():
    """立即检查一次邮箱（同步执行，最多约 30 秒）。"""
    logger.info("手动触发邮箱检查")
    count = email_ingest.poll_once()
    return {"ok": True, "count": count, "status": email_ingest.get_status()}
