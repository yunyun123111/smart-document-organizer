"""FastAPI 应用入口。

启动时：
1. 初始化日志系统
2. 初始化数据库（建表 + 默认分类）
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import hmac
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.auth import access_token, router as auth_router
from backend.api.backup import router as backup_router
from backend.api.categories import router as categories_router
from backend.api.email import router as email_router
from backend.api.documents import router as documents_router
from backend.api.excel_sources import router as excel_sources_router
from backend.api.filename_rules import router as filename_rules_router
from backend.api.logs import router as logs_router
from backend.api.processing import router as processing_router
from backend.api.recycle_bin import router as recycle_bin_router
from backend.api.review import router as review_router
from backend.api.rules import router as rules_router
from backend.api.samples import router as samples_router
from backend.api.settings import router as settings_router
from backend.api.system import router as system_router
from backend.config import settings
from backend.database import init_db
from backend.utils.logger import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_dir, settings.LOG_LEVEL)
    logger = get_logger("main")
    logger.info("=== %s 启动 (env=%s) ===", settings.APP_NAME, settings.APP_ENV)
    init_db()
    # 邮箱接收轮询（后台线程，配置变更后也可动态启停）
    try:
        from backend.services.email_ingest import sync_from_settings

        sync_from_settings()
    except Exception:
        logger.exception("邮箱接收启动失败，本轮不启用")
    yield
    logger.info("=== %s 退出 ===", settings.APP_NAME)


app = FastAPI(
    title="Smart Document Organizer API",
    description="本地优先的智能文档自动整理工具",
    version="1.0.0",
    lifespan=lifespan,
)

# 注册路由
app.include_router(auth_router)
app.include_router(email_router)
app.include_router(system_router)
app.include_router(categories_router)
app.include_router(documents_router)
app.include_router(filename_rules_router)
app.include_router(processing_router)
app.include_router(review_router)
app.include_router(recycle_bin_router)
app.include_router(excel_sources_router)
app.include_router(rules_router)
app.include_router(samples_router)
app.include_router(settings_router)
app.include_router(backup_router)
app.include_router(logs_router)


# ---- 访问密码中间件（局域网开放时保护所有 /api 接口）----
_OPEN_PATHS = ("/api/auth/", "/api/system/health")


@app.middleware("http")
async def access_password_middleware(request: Request, call_next):
    if settings.ACCESS_PASSWORD and request.url.path.startswith("/api/"):
        if not any(request.url.path.startswith(prefix) for prefix in _OPEN_PATHS):
            auth = request.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            if not hmac.compare_digest(token, access_token()):
                from fastapi.responses import JSONResponse

                return JSONResponse(status_code=401, content={"detail": "需要访问密码"})
    return await call_next(request)


# ---- 前端静态托管（生产单进程模式）----
# 若存在构建产物 frontend/dist，则由本后端直接托管前端页面，
# 访问 http://127.0.0.1:8000 即可，无需再单独启动前端 dev server。
BASE_DIR = Path(__file__).resolve().parent.parent
_FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def _serve_spa(full_path: str):
        # 非 /api 的路径交给前端路由；存在真实文件则返回文件，否则回退 index.html
        if full_path:
            candidate = _FRONTEND_DIST / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
