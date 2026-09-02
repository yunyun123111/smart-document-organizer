"""归档服务（规格书 Phase 10 / 第二十九/三十四节）。

归档流程：
  验证路径 → 生成目录（分类/年/月）→ 检查文件名唯一 → SHA256 去重
  → 移动文件 → 记录日志
任一步失败都保证原文件不动，返回明确错误信息。

并发说明：批量整理用线程池并发处理文件，SHA256 判重与"移动 + 落库"
必须作为一个整体串行执行，否则两个相同文件会在双方都未提交前
各自通过判重、产生重复归档。这里用归档锁保护该临界区
（解析 / OCR / AI 等耗时步骤仍在锁外并发）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import (
    STATUS_ARCHIVED,
    Document,
    OP_ARCHIVE,
    RESULT_FAILED,
    RESULT_OK,
)
from backend.services import operation_service
from backend.services.duplicate_service import duplicate_service
from backend.services.file_service import FileOperationError, ensure_directory, move_file
from backend.services.rename_service import rename_service
from backend.utils.file_utils import get_file_type, safe_join
from backend.utils.filename_utils import safe_filename
from backend.utils.logger import get_logger

logger = get_logger("services.archive_service")


@dataclass
class ArchiveResult:
    success: bool
    document_path: Path | None = None
    document_id: int | None = None
    duplicate: bool = False
    duplicate_of: Document | None = None
    error: str = ""


def _safe_target_name(filename: str, src: Path) -> str:
    """把目标文件名清洗为安全名，并保证扩展名与原文件一致。

    filename 可能来自 AI 渲染的模板结果，也可能来自人工审核的请求体：
    - 已带扩展名则沿用，否则补原文件扩展名；
    - 非法字符与路径分隔符统一由 safe_filename 清除。
    """
    raw = (filename or "").strip() or src.name
    p = Path(raw)
    return safe_filename(p.stem or src.stem, p.suffix or src.suffix)


class ArchiveService:
    #: 保护"判重 → 移动 → 落库"临界区，避免并发批量任务归档重复文件
    _archive_lock = threading.Lock()

    def __init__(
        self,
        db: Session,
        document_root: str | Path | None = None,
        allow_overwrite: bool | None = None,
    ):
        self.db = db
        self.document_root = (
            Path(document_root) if document_root else settings.document_root
        )
        self.allow_overwrite = (
            allow_overwrite
            if allow_overwrite is not None
            else settings.ALLOW_OVERWRITE
        )

    def build_target_dir(self, category_path: str, date_str: str | None = None) -> Path:
        """生成归档目录：DOCUMENT_ROOT / 分类路径 / 年 / 月（规格书第二十九节）。

        category_path 可能来自请求体（人工审核可指定分类），必须做路径穿越防护：
        禁止 .. / 绝对路径逃出 document_root。
        """
        rel = (category_path or "其他").strip().strip("/\\")
        if not rel:
            rel = "其他"
        base = safe_join(self.document_root, rel)  # 非法（越界/绝对）时抛 ValueError
        if date_str:
            try:
                d = datetime.strptime(date_str[:10], "%Y-%m-%d")
                base = base / str(d.year) / f"{d.month:02d}"
            except (ValueError, TypeError):
                pass  # 日期非法则忽略年/月目录
        return base

    def archive(
        self,
        src_path: str | Path,
        category_path: str,
        filename: str,
        date_str: str | None = None,
        document_id: int | None = None,
        job_id: int | None = None,
    ) -> ArchiveResult:
        """归档单个文件。category_path 为分类相对路径（如 合同/销售合同）。"""
        src = Path(src_path)
        if not src.exists():
            return ArchiveResult(success=False, error=f"源文件不存在: {src}")

        # 1. 计算 hash（耗时，放在锁外）
        file_hash = duplicate_service.file_hash(src)

        # 2. 判重 → 移动 → 落库：临界区，防止并发归档同一内容的文件
        with self._archive_lock:
            return self._archive_locked(
                src, file_hash, category_path, filename, date_str, document_id, job_id
            )

    def _archive_locked(
        self,
        src: Path,
        file_hash: str,
        category_path: str,
        filename: str,
        date_str: str | None,
        document_id: int | None,
        job_id: int | None,
    ) -> ArchiveResult:
        # 分类与文件名都可能来自人工审核的请求体：先做安全清洗与穿越校验
        try:
            target_dir = self.build_target_dir(category_path, date_str)
        except ValueError as e:
            logger.warning("非法归档分类路径: %r", category_path)
            return ArchiveResult(success=False, error=f"分类路径非法: {e}")
        safe_name = _safe_target_name(filename, src)
        if (Path(safe_name).name != safe_name) or safe_name in (".", ".."):
            return ArchiveResult(success=False, error=f"文件名非法: {filename!r}")

        # SHA256 去重（需与已归档 Document 记录比对）
        # 先提交：结束本会话的读事务快照，否则在 WAL 下看不到其它 worker
        # 刚刚提交的 archived 记录，判重会失效。
        # （此时 session 里是本次识别出的字段与状态，本就该落库）
        self.db.commit()
        # exclude_id=document_id：排除正在归档的文档自身（它处于 need_review，
        # 不排除会命中自己而误判重复，导致人工审核归档全部失败）。
        dup = duplicate_service.find_duplicate(self.db, file_hash, exclude_id=document_id)
        if dup is not None:
            logger.info("重复文件，跳过归档: %s (对应文档#%s)", src.name, dup.id)
            return ArchiveResult(
                success=False, duplicate=True, duplicate_of=dup, document_path=None
            )

        # 归档记录必须已存在：否则拒绝归档，而不是搬完文件再插一条空记录
        doc: Document
        if document_id is not None:
            found = self.db.get(Document, document_id)
            if found is None:
                return ArchiveResult(
                    success=False, error=f"文档记录不存在，拒绝归档: document_id={document_id}"
                )
            doc = found
        else:
            doc = Document(
                original_filename=src.name,
                original_path=str(src),
                file_type=get_file_type(src),
                file_size=src.stat().st_size if src.exists() else 0,
            )
            self.db.add(doc)
            self.db.flush()  # 先拿到 id，让日志能关联到文档（撤销依赖它）

        ensure_directory(target_dir)

        # 唯一文件名（禁止覆盖；allow_overwrite=True 时改为覆盖旧文件）
        target = rename_service.build_unique_path(
            target_dir, safe_name, allow_overwrite=self.allow_overwrite
        )

        # 移动文件
        try:
            moved = move_file(src, target, allow_overwrite=self.allow_overwrite)
        except FileOperationError as e:
            operation_service.log_operation(
                self.db,
                OP_ARCHIVE,
                old_path=str(src),
                new_path=str(target),
                document_id=doc.id,
                job_id=job_id,
                result=RESULT_FAILED,
                error_message=str(e),
            )
            logger.error("归档失败: %s", e)
            return ArchiveResult(success=False, error=str(e))

        # 更新文档状态；失败则把文件搬回原处，绝不留下"文件已归档但库里不知道"的状态
        try:
            doc.file_hash = file_hash
            doc.status = STATUS_ARCHIVED
            doc.current_filename = moved.name
            doc.current_path = str(moved)
            doc.file_type = get_file_type(moved)
            doc.processed_at = datetime.now()
            self.db.commit()
        except Exception as e:  # noqa: BLE001
            self.db.rollback()
            logger.error("归档落库失败，回搬文件: %s", e)
            try:
                move_file(moved, src)
            except FileOperationError as e2:  # noqa: BLE001
                logger.critical("归档回搬失败，文件位于 %s: %s", moved, e2)
            return ArchiveResult(
                success=False, error=f"归档记录写入失败（文件已退回原位置）: {e}"
            )

        # 记录日志
        operation_service.log_operation(
            self.db,
            OP_ARCHIVE,
            old_path=str(src),
            new_path=str(moved),
            document_id=doc.id,
            job_id=job_id,
            result=RESULT_OK,
        )

        logger.info("归档完成: %s -> %s (doc#%s)", src, moved, doc.id)
        return ArchiveResult(success=True, document_path=moved, document_id=doc.id)


# 说明：ArchiveService 的构造函数需要 db，因此这里导出的是类本身（按需实例化），
# 不是实例。为避免误用，不提供 archive_service.archive(...) 这种模块级别名。
__all__ = ["ArchiveService", "ArchiveResult"]
