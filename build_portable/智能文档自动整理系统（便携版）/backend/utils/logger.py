"""日志系统。

两类日志分开：
- 系统运行日志：data/logs/system.log（DEBUG/INFO/WARNING/ERROR）
- 文件操作日志：data/logs/operations.log（所有文件移动/重命名/归档/撤销操作）
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 日志文件最大 5MB，保留 5 个备份
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_loggers: dict[str, logging.Logger] = {}
_configured = False


def _make_handler(path: Path, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    handler.setLevel(level)
    return handler


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """初始化日志目录与系统日志记录器（幂等）。"""
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("sdo")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False

    # 文件 handler
    root.addHandler(_make_handler(log_dir / "system.log", logging.DEBUG))
    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(console)

    _configured = True


def get_logger(name: str = "sdo") -> logging.Logger:
    """获取系统运行日志记录器。name 建议用模块路径，如 services.parser_service。"""
    if name in _loggers:
        return _loggers[name]
    logger = logging.getLogger(f"sdo.{name}" if name != "sdo" else "sdo")
    _loggers[name] = logger
    return logger


def get_operation_logger(log_dir: Path | None = None) -> logging.Logger:
    """获取独立的文件操作日志记录器（写入 operations.log）。

    所有文件移动/重命名/归档/撤销等操作必须通过该记录器记录，
    保证文件操作日志与系统运行日志分开、可独立审计。
    """
    key = "operation"
    if key in _loggers:
        return _loggers[key]

    logger = logging.getLogger("sdo.operations")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = Path(log_dir) if log_dir else Path("data/logs")
    target.mkdir(parents=True, exist_ok=True)
    logger.addHandler(_make_handler(target / "operations.log", logging.INFO))
    # 操作日志也输出到控制台，便于本地调试观察
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s | OPERATION | %(message)s", _DATE_FORMAT))
    console.setLevel(logging.INFO)
    logger.addHandler(console)

    _loggers[key] = logger
    return logger
