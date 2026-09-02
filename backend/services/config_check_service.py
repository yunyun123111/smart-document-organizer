"""配置状态校验服务（P0-5）。

对关键配置逐项检查，返回健康状态，用于设置页顶部展示配置健康度，
避免「配置错误导致功能静默失效」：
- 目录失效 → 文件整理/归档静默失败
- AI 启用但缺 Key/地址 → 识别回退规则，用户不知情
- 邮箱启用但配置不全 → 永远收不到附件
- 局域网开放但无访问密码 → AI Key / 文档可被同网段访问
- 阈值关系异常 → 所有文件都进人工审核
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger("services.config_check")

# 级别：ok / warn / error / info
_LEVEL_OK = "ok"
_LEVEL_WARN = "warn"
_LEVEL_ERROR = "error"
_LEVEL_INFO = "info"


def _is_writable(path: Path) -> bool:
    """检查目录是否可写。"""
    try:
        probe = path / f".write_probe_{id(path)}"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def _check_document_root() -> dict:
    path = Path(settings.DOCUMENT_ROOT)
    if not path.exists():
        return {"key": "document_root", "label": "文档根目录", "level": _LEVEL_WARN,
                "message": "目录不存在，归档将失败", "detail": str(path)}
    if not _is_writable(path):
        return {"key": "document_root", "label": "文档根目录", "level": _LEVEL_WARN,
                "message": "目录不可写，无法归档文件", "detail": str(path)}
    return {"key": "document_root", "label": "文档根目录", "level": _LEVEL_OK,
            "message": "正常", "detail": str(path)}


def _check_inbox_root() -> dict:
    path = Path(settings.INBOX_ROOT)
    if not path.exists():
        return {"key": "inbox_root", "label": "待整理目录", "level": _LEVEL_WARN,
                "message": "目录不存在，批量整理将失败", "detail": str(path)}
    if not _is_writable(path):
        return {"key": "inbox_root", "label": "待整理目录", "level": _LEVEL_WARN,
                "message": "目录不可写", "detail": str(path)}
    return {"key": "inbox_root", "label": "待整理目录", "level": _LEVEL_OK,
            "message": "正常", "detail": str(path)}


def _check_ocr() -> dict:
    if not settings.OCR_ENABLED:
        return {"key": "ocr", "label": "OCR 识别", "level": _LEVEL_INFO,
                "message": "已关闭，扫描件/图片将不识别", "detail": "OCR_ENABLED=false"}
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return {"key": "ocr", "label": "OCR 识别", "level": _LEVEL_OK,
                "message": "RapidOCR 本地模型可用", "detail": "rapidocr-onnxruntime"}
    except Exception as e:  # noqa: BLE001
        return {"key": "ocr", "label": "OCR 识别", "level": _LEVEL_WARN,
                "message": "OCR 依赖加载失败，扫描件无法识别", "detail": str(e)[:120]}


def _check_ai() -> dict:
    if not settings.AI_ENABLED:
        return {"key": "ai", "label": "AI 辅助识别", "level": _LEVEL_INFO,
                "message": "已关闭，仅用规则/字段识别", "detail": "AI_ENABLED=false"}
    missing = []
    if not settings.AI_BASE_URL:
        missing.append("API 地址")
    if not settings.AI_API_KEY:
        missing.append("API Key")
    if not settings.AI_MODEL:
        missing.append("模型名")
    if missing:
        return {"key": "ai", "label": "AI 辅助识别", "level": _LEVEL_WARN,
                "message": f"已启用但缺少：{'、'.join(missing)}，AI 将回退规则识别",
                "detail": "、".join(missing)}
    return {"key": "ai", "label": "AI 辅助识别", "level": _LEVEL_OK,
            "message": "配置完整", "detail": settings.AI_PROVIDER}


def _check_email() -> dict:
    if not settings.EMAIL_ENABLED:
        return {"key": "email", "label": "邮箱接收", "level": _LEVEL_INFO,
                "message": "已关闭", "detail": "EMAIL_ENABLED=false"}
    missing = []
    if not settings.EMAIL_IMAP_HOST:
        missing.append("IMAP 服务器")
    if not settings.EMAIL_USER:
        missing.append("邮箱账号")
    if not settings.EMAIL_PASSWORD:
        missing.append("授权码")
    if missing:
        return {"key": "email", "label": "邮箱接收", "level": _LEVEL_ERROR,
                "message": f"已启用但缺少：{'、'.join(missing)}，无法收取附件",
                "detail": "、".join(missing)}
    return {"key": "email", "label": "邮箱接收", "level": _LEVEL_OK,
            "message": "配置完整", "detail": settings.EMAIL_IMAP_HOST}


def _check_security() -> dict:
    host = (settings.HOST_BIND or "").strip()
    exposed = host not in ("", "127.0.0.1", "localhost")
    if exposed and not settings.ACCESS_PASSWORD:
        return {"key": "security", "label": "访问安全", "level": _LEVEL_WARN,
                "message": "已开放局域网访问但未设置访问密码，同网段设备可直接访问（含 AI Key 配置）",
                "detail": f"HOST_BIND={host}, ACCESS_PASSWORD 为空"}
    if exposed and settings.ACCESS_PASSWORD:
        return {"key": "security", "label": "访问安全", "level": _LEVEL_OK,
                "message": "局域网访问已设密码", "detail": f"HOST_BIND={host}"}
    return {"key": "security", "label": "访问安全", "level": _LEVEL_OK,
            "message": "仅本机访问", "detail": "HOST_BIND=127.0.0.1"}


def _check_thresholds() -> dict:
    auto = settings.AUTO_ARCHIVE_THRESHOLD
    review = settings.REVIEW_THRESHOLD
    if auto is not None and review is not None and auto <= review:
        return {"key": "thresholds", "label": "识别阈值", "level": _LEVEL_WARN,
                "message": "自动归档阈值应大于人工确认阈值，否则所有文件都进人工审核",
                "detail": f"auto={auto}, review={review}"}
    return {"key": "thresholds", "label": "识别阈值", "level": _LEVEL_OK,
            "message": "正常", "detail": f"auto={auto}, review={review}"}


def _check_database(db) -> dict:
    try:
        has_migrations = db.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
        ).fetchone() is not None
        has_documents = db.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'")
        ).fetchone() is not None
        if not has_documents:
            return {"key": "database", "label": "数据库", "level": _LEVEL_ERROR,
                    "message": "documents 表缺失，系统不可用", "detail": "documents 表不存在"}
        return {"key": "database", "label": "数据库", "level": _LEVEL_OK,
                "message": "正常" + ("，迁移机制已启用" if has_migrations else ""),
                "detail": "SQLite"}
    except Exception as e:  # noqa: BLE001
        return {"key": "database", "label": "数据库", "level": _LEVEL_ERROR,
                "message": "数据库连接失败", "detail": str(e)[:120]}


def run_config_checks(db=None) -> dict:
    """执行全部配置检查。db 为空时跳过数据库项。"""
    checks = [
        _check_document_root(),
        _check_inbox_root(),
        _check_ocr(),
        _check_ai(),
        _check_email(),
        _check_security(),
        _check_thresholds(),
    ]
    if db is not None:
        checks.append(_check_database(db))

    levels = {c["level"] for c in checks}
    if _LEVEL_ERROR in levels:
        summary = "error"
    elif _LEVEL_WARN in levels:
        summary = "warn"
    else:
        summary = "ok"
    return {
        "summary": summary,
        "ok_count": sum(1 for c in checks if c["level"] == _LEVEL_OK),
        "warn_count": sum(1 for c in checks if c["level"] == _LEVEL_WARN),
        "error_count": sum(1 for c in checks if c["level"] == _LEVEL_ERROR),
        "checks": checks,
    }
