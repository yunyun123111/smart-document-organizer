"""哈希工具：文件 SHA256 计算（重复文件检测用）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1MB


def sha256_file(path: str | Path) -> str:
    """流式计算文件 SHA256，避免大文件一次性读入内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """计算字节内容 SHA256。"""
    return hashlib.sha256(data).hexdigest()
