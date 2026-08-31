"""访问认证 API：局域网访问密码校验。

- GET  /api/auth/status  是否需要密码
- POST /api/auth/login   校验密码，返回访问令牌
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import settings
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger("api.auth")

_TOKEN_SALT = "smart-doc-organizer::access"


def access_token() -> str:
    """根据访问密码计算稳定令牌（密码不变则令牌不变，客户端可长期保存）。"""
    pw = settings.ACCESS_PASSWORD or ""
    return hmac.new(_TOKEN_SALT.encode(), pw.encode(), hashlib.sha256).hexdigest()


class LoginRequest(BaseModel):
    password: str


@router.get("/status")
def auth_status():
    return {"password_required": bool(settings.ACCESS_PASSWORD)}


@router.post("/login")
def login(req: LoginRequest):
    if not settings.ACCESS_PASSWORD:
        raise HTTPException(status_code=400, detail="系统未启用访问密码")
    # 恒定时间比较，避免时序攻击
    if not hmac.compare_digest(req.password, settings.ACCESS_PASSWORD):
        logger.warning("访问密码校验失败")
        raise HTTPException(status_code=401, detail="密码错误")
    logger.info("访问认证通过")
    return {"ok": True, "token": access_token()}
