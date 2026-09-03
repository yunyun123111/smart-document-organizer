"""文件名安全工具（规格书第三十一节）。

自动处理非法字符：\\ / : * ? " < > |
限制文件名长度；重名递增 _001；禁止覆盖已有文件（由上层控制）。
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.utils.fs_path import fs_exists

# Windows 文件名非法字符
ILLEGAL_CHARS = r'\\/:*?"<>|'
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 保留字（Windows 设备名）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 文件名最大长度（不含扩展名，保守取值避免 260 路径上限）
MAX_BASENAME_LENGTH = 120


def sanitize_filename(name: str, max_length: int = MAX_BASENAME_LENGTH) -> str:
    """清理文件名基名（不含扩展名）：
    - 去除非法字符
    - 合并连续空格、去首尾空格
    - 去除首尾点
    - 限制长度
    """
    if not name:
        return "未命名"
    name = _ILLEGAL_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    # 去除 Windows 保留字
    stem = name.upper()
    if stem in _RESERVED:
        name = f"_{name}"
    if len(name) > max_length:
        name = name[:max_length].rstrip(" .")
    return name or "未命名"


def safe_filename(basename: str, ext: str = "") -> str:
    """生成安全完整文件名：清理基名 + 规范化扩展名。"""
    base = sanitize_filename(basename)
    if ext:
        ext = ext if ext.startswith(".") else f".{ext}"
        ext = _ILLEGAL_RE.sub("", ext).lower()
        # 扩展名也限制长度（一般 10 以内）
        ext = ext[:10]
    return f"{base}{ext}"


def unique_filename(directory: Path, filename: str, allow_overwrite: bool = False) -> Path:
    """在 directory 中为 filename 生成不冲突的路径。

    - allow_overwrite=True：直接返回目标（上层自行决定是否覆盖）
    - 否则重名自动递增：文件.pdf -> 文件_001.pdf -> 文件_002.pdf
    """
    target = directory / filename
    if allow_overwrite or not fs_exists(target):
        return target

    stem = Path(filename).stem
    ext = Path(filename).suffix
    for i in range(1, 10000):
        candidate = directory / f"{stem}_{i:03d}{ext}"
        if not fs_exists(candidate):
            return candidate
    # 极端情况：递增到上限仍冲突，加时间戳
    import time

    return directory / f"{stem}_{int(time.time())}{ext}"
