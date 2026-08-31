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

from backend.utils.logger import get_operation_logger, get_logger

logger = get_logger("services.file_service")
op_logger = get_operation_logger()


class FileOperationError(Exception):
    """文件操作失败。"""


def ensure_directory(path: Path) -> Path:
    """确保目录存在并返回。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def move_file(src: str | Path, dst: str | Path, allow_overwrite: bool = False) -> Path:
    """移动文件（同一卷优先原子 rename，跨卷回退 copy+delete）。

    - 确保目标目录存在
    - 默认不覆盖已有文件；allow_overwrite=True 时显式允许覆盖
    - 任何异常抛出 FileOperationError，原文件保持不动
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise FileOperationError(f"源文件不存在: {src}")
    if src == dst:
        return dst

    ensure_directory(dst.parent)

    # 目标已存在且不是同一文件
    backup: Path | None = None
    if dst.exists():
        if not allow_overwrite:
            raise FileOperationError(f"目标已存在，拒绝覆盖: {dst}")
        # 显式允许覆盖：先把旧文件原子改名备份，成功落位后再删除，
        # 失败则改名回去 —— 保证任何一步中断都不会丢文件。
        backup = dst.with_name(f"{dst.name}.{int(time.time() * 1000)}.replacing")
        try:
            dst.rename(backup)
        except OSError as e:
            raise FileOperationError(f"覆盖前备份目标文件失败: {e}") from e

    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        # 跨卷移动失败时回退复制+删除
        try:
            shutil.copy2(str(src), str(dst))
            src.unlink()
        except OSError as e2:
            # 复制失败：清理可能产生的半成品
            if dst.exists():
                dst.unlink(missing_ok=True)
            # 恢复被改名的旧文件
            if backup is not None and backup.exists() and not dst.exists():
                try:
                    backup.rename(dst)
                except OSError:
                    logger.critical("覆盖失败且旧文件备份留在 %s，未丢失", backup)
            raise FileOperationError(f"移动文件失败: {e} / {e2}") from e2

    if backup is not None:
        try:
            backup.unlink(missing_ok=True)
        except OSError as e:  # noqa: BLE001
            logger.warning("清理覆盖备份失败（旧文件留在 %s）: %s", backup, e)

    logger.info("移动文件: %s -> %s%s", src, dst, "（覆盖）" if backup else "")
    return dst


def copy_file(src: str | Path, dst: str | Path) -> Path:
    """复制文件到目标（不删除源）。"""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise FileOperationError(f"源文件不存在: {src}")
    ensure_directory(dst.parent)
    if dst.exists():
        raise FileOperationError(f"目标已存在，拒绝覆盖: {dst}")
    shutil.copy2(str(src), str(dst))
    logger.info("复制文件: %s -> %s", src, dst)
    return dst


def delete_file(path: str | Path) -> None:
    """删除文件（仅用于临时文件/复制产物清理，绝不用作归档）。"""
    Path(path).unlink(missing_ok=True)
