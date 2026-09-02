"""文件工具：文件类型检测、路径安全辅助。

V1 支持的文件类型映射集中在这里，后续新增类型只需扩展映射表。
"""
from __future__ import annotations

from pathlib import Path

# 扩展名 -> 标准文件类型（统一小写，不含点）
# 注意：doc / xls 在 V1 只登记建档、没有解析器（旧版二进制格式），
# 解析时会得到明确错误并进入人工审核；见 ParserService.parse_file。
SUPPORTED_TYPES: dict[str, str] = {
    ".pdf": "pdf",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".png": "png",
    ".doc": "doc",
    ".docx": "docx",
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".txt": "txt",
    ".csv": "csv",
}

# MIME 类型（基础映射，供记录与前端使用）
MIME_MAP: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "png": "image/png",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "csv": "text/csv",
}

# 图片类类型（需要走 OCR 的）
IMAGE_TYPES = {"jpg", "png"}

# 可解析文本类类型
TEXT_PARSABLE_TYPES = {"pdf", "docx", "xlsx", "txt", "csv"}


def get_file_type(path: str | Path) -> str:
    """根据扩展名返回标准文件类型；不支持的扩展名返回 'unknown'。"""
    return SUPPORTED_TYPES.get(Path(path).suffix.lower(), "unknown")


def is_supported(path: str | Path) -> bool:
    """判断文件类型是否在 V1 支持范围内。"""
    return get_file_type(path) != "unknown"


def get_mime_type(file_type: str) -> str:
    """返回标准文件类型对应的 MIME；未知返回 application/octet-stream。"""
    return MIME_MAP.get(file_type, "application/octet-stream")


def safe_join(base: Path, *parts: str) -> Path:
    """在 base 目录下安全拼接路径，防止路径穿越（.. 等）。"""
    p = base.joinpath(*parts).resolve()
    base_resolved = base.resolve()
    if not p.is_relative_to(base_resolved):
        raise ValueError(f"路径越界: {p}")
    return p
