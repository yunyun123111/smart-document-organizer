"""Windows 长路径与只读目录支持（V1.0 稳定性修复·任务3）。

背景：CPython 的 os/pathlib 在 Windows 上对超长路径（>=260 字符）**不会自动**
加 extended-length 前缀（实测 Python 3.13 win32 直接写入 268 字符路径抛
FileNotFoundError），而显式加前缀后普通 Path.exists() 又看不到该文件。因此
必须统一：所有**实际文件系统调用**与**存在性检查**都经过本模块的 fs_* 函数。

- fs_path():   仅 Windows 且路径超长时转 extended-length 前缀（绝对路径），其余原样
- fs_exists/fs_isfile/fs_mkdir: 带长路径支持的检查与建目录
- ensure_writable(): 只读/不可用目录检测，返回明确错误信息
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

# Windows 经典 MAX_PATH（不含结尾 NUL）。>259 即触发前缀转换。
_WIN_MAX_PATH = 259


def fs_path(path: str | Path) -> str:
    r"""把路径转换为适合实际文件系统调用的字符串。

    仅在 Windows 且路径超过 259 字符时添加 extended-length 前缀（绝对路径），
    短路径/其他平台保持原样返回，避免前缀带来的兼容性问题。
    """
    s = str(path)
    if os.name != "nt":
        return s
    if len(s) <= _WIN_MAX_PATH:
        return s
    if s.startswith("\\\\?\\") or s.startswith("\\\\.\\"):
        return s  # 已带前缀
    if s.startswith("\\\\"):  # UNC 路径
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def fs_exists(path: str | Path) -> bool:
    r"""长路径兼容的存在性检查（普通 Path.exists 看不到前缀写入的文件）。"""
    return os.path.exists(fs_path(path))


def fs_isfile(path: str | Path) -> bool:
    return os.path.isfile(fs_path(path))


def fs_isdir(path: str | Path) -> bool:
    return os.path.isdir(fs_path(path))


def fs_mkdir(path: str | Path, parents: bool = True, exist_ok: bool = True) -> None:
    """长路径兼容的建目录（自动创建父目录）。"""
    os.makedirs(fs_path(path), exist_ok=exist_ok)


def ensure_writable(path: str | Path) -> str:
    """检测目录是否可写/可用。

    返回 '' 表示可写；否则返回明确错误信息（供前端展示，替代 500/无意义错误）。
    检测方式：确保目录存在 -> 创建并删除探测文件。Windows 目录只读属性
    未必阻止 NTFS 创建文件，因此探测文件是最可靠的判据。
    """
    p = Path(path)
    try:
        fs_mkdir(p)
        if not fs_isdir(p):
            return f"路径不是目录: {p}"
        probe = p / f".sdo_wprobe_{os.getpid()}_{int(time.time() * 1000)}_{random.randrange(10000)}"
        try:
            with open(fs_path(probe), "wb") as f:
                f.write(b"")
        finally:
            try:
                os.unlink(fs_path(probe))
            except OSError:
                pass
        return ""
    except OSError as e:
        detail = e.strerror or str(e)
        return f"目录不可写: {p}（{detail}）"


def path_too_long(path: str | Path, limit: int = 250) -> bool:
    """判断路径是否接近 Windows 260 上限（返回 True 表示建议启用长路径处理）。"""
    return len(str(path)) > limit
