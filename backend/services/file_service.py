"""文件操作服务（规格书第六节文件安全 + 规则9）。

所有文件移动/重命名/归档操作统一经过本服务，保证：
- 默认移动而非复制+删除
- 操作全程记录日志（数据库 OperationLog + operations.log 文件）
- 失败时尽量回滚，绝不丢失原文件
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from backend.utils.fs_path import fs_exists, fs_isfile, fs_mkdir, fs_path
from backend.utils.logger import get_operation_logger, get_logger

logger = get_logger("services.file_service")
op_logger = get_operation_logger()


class FileOperationError(Exception):
    """文件操作失败。"""


def ensure_directory(path: Path) -> Path:
    """确保目录存在并返回（支持 Windows 长路径）。"""
    fs_mkdir(path)
    return path


def move_file(src: str | Path, dst: str | Path, allow_overwrite: bool = False) -> Path:
    """移动文件（同一卷优先原子 rename，跨卷回退 copy+delete）。

    - 确保目标目录存在
    - 默认不覆盖已有文件；allow_overwrite=True 时显式允许覆盖
    - 任何异常抛出 FileOperationError，原文件保持不动
    """
    src = Path(src)
    dst = Path(dst)
    if not fs_isfile(src):
        raise FileOperationError(f"源文件不存在: {src}")
    if src == dst:
        return dst

    try:
        fs_mkdir(dst.parent)
    except OSError as e:
        detail = getattr(e, "strerror", None) or e
        raise FileOperationError(f"归档目录不可用（可能没有写入权限）: {dst.parent}（{detail}）")

    # 目标已存在且不是同一文件
    backup: Path | None = None
    if fs_exists(dst):
        if not allow_overwrite:
            raise FileOperationError(f"目标已存在，拒绝覆盖: {dst}")
        # 显式允许覆盖：先把旧文件原子改名备份，成功落位后再删除，
        # 失败则改名回去 —— 保证任何一步中断都不会丢文件。
        backup = dst.with_name(f"{dst.name}.{int(time.time() * 1000)}.replacing")
        try:
            os_replace(dst, backup)
        except OSError as e:
            raise FileOperationError(f"覆盖前备份目标文件失败: {e}") from e

    try:
        shutil.move(fs_path(src), fs_path(dst))
    except OSError as e:
        # 跨卷移动失败时回退复制+删除
        try:
            shutil.copy2(fs_path(src), fs_path(dst))
            os_unlink(src)
        except OSError as e2:
            # 复制失败：清理可能产生的半成品
            if fs_exists(dst):
                os_unlink(dst)
            # 恢复被改名的旧文件
            if backup is not None and fs_exists(backup) and not fs_exists(dst):
                try:
                    os_replace(backup, dst)
                except OSError:
                    logger.critical("覆盖失败且旧文件备份留在 %s，未丢失", backup)
            raise FileOperationError(f"移动文件失败: {e} / {e2}") from e2

    if backup is not None:
        try:
            os_unlink(backup)
        except OSError as e:  # noqa: BLE001
            logger.warning("清理覆盖备份失败（旧文件留在 %s）: %s", backup, e)

    logger.info("移动文件: %s -> %s%s", src, dst, "（覆盖）" if backup else "")
    return dst


def copy_file(src: str | Path, dst: str | Path) -> Path:
    """复制文件到目标（不删除源）。"""
    src, dst = Path(src), Path(dst)
    if not fs_isfile(src):
        raise FileOperationError(f"源文件不存在: {src}")
    ensure_directory(dst.parent)
    if fs_exists(dst):
        raise FileOperationError(f"目标已存在，拒绝覆盖: {dst}")
    shutil.copy2(fs_path(src), fs_path(dst))
    logger.info("复制文件: %s -> %s", src, dst)
    return dst


def delete_file(path: str | Path) -> None:
    """删除文件（仅用于临时文件/复制产物清理，绝不用作归档）。"""
    try:
        os_unlink(Path(path))
    except OSError:
        pass


# ---- 长路径兼容的底层原子操作（统一 fs_path）----
def os_replace(src: Path, dst: Path) -> None:
    """原子替换（长路径兼容）。"""
    import os

    os.replace(fs_path(src), fs_path(dst))


def os_unlink(path: Path) -> None:
    """删除文件（长路径兼容，missing_ok 语义）。"""
    import os

    try:
        os.unlink(fs_path(path))
    except FileNotFoundError:
        pass
