"""批量整理服务（规格书 Phase 13 / 第四十六节）。

- 扫描源目录 → 创建任务（ProcessingJob）
- 后台线程池逐文件处理：登记 → 识别 → 按决策归档/待审核/重复/失败
- 任务进度实时写入数据库，前端轮询获取
- 支持取消
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models import (
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_FAILED,
    STATUS_NEED_REVIEW,
    STATUS_PENDING,
    STATUS_PROCESSING,
    Document,
    DocumentField,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_RUNNING,
    OP_CLASSIFY,
    OP_IMPORT,
    ProcessingJob,
    SOURCE_RULE,
)
from backend.services.archive_service import ArchiveService
from backend.services.classifier import ClassifierService
from backend.services.duplicate_service import duplicate_service
from backend.services.operation_service import log_operation
from backend.utils.file_utils import get_file_type, get_mime_type
from backend.utils.fs_path import ensure_writable, fs_exists, fs_isfile
from backend.utils.hash_utils import sha256_file
from backend.utils.logger import get_logger

logger = get_logger("services.processing_service")

# 并发 worker 数（OCR/AI 为主要耗时；SQLite WAL 支持并发写）
MAX_WORKERS = 4


class ProcessingService:
    def __init__(self):
        self._jobs: dict[int, threading.Event] = {}  # job_id -> cancel 事件
        self._lock = threading.Lock()

    # ---------- 任务控制 ----------
    def start_job(self, source_dir: str | None = None) -> ProcessingJob:
        """创建任务并开始后台处理。返回任务。"""
        if self._has_running():
            raise RuntimeError("已有整理任务正在运行，请等待其完成后再开始")
        src = Path(source_dir) if source_dir else settings.inbox_root
        src = src.resolve()
        # 回收站目录绝不能作为待整理目录，避免把已删除文件重新识别归档
        recycle_root = settings.recycle_bin_root.resolve()
        if src == recycle_root or recycle_root in src.parents:
            raise ValueError("回收站目录不能作为待整理目录")
        if not fs_exists(src):
            raise FileNotFoundError(f"待整理目录不存在: {src}")
        # inbox 只读/不可用：阻止整理任务启动，给出明确提示
        writable_err = ensure_writable(src)
        if writable_err:
            raise PermissionError(f"无法启动整理任务：{writable_err}")

        # 跳过隐藏文件（.xxx）与 Office 临时文件（~$xxx），避免误扫与浪费
        files = [
            p for p in src.iterdir()
            if fs_isfile(p)
            and not p.name.startswith((".", "~$"))
            and get_file_type(p) != "unknown"
        ]

        # 只跳过已定性的文件（人工审核中 / 已归档 / 重复），
        # 避免人工审核中的文件被再次识别（浪费 token/OCR）。
        # pending/processing 是待本次批量处理的文件，必须保留。
        if files:
            with SessionLocal() as db:
                skip = {
                    r[0]
                    for r in db.query(Document.original_path)
                    .filter(
                        Document.status.in_(
                            (STATUS_NEED_REVIEW, STATUS_ARCHIVED, STATUS_DUPLICATE)
                        )
                    )
                    .all()
                    if r[0]
                }
            files = [
                p for p in files
                if str(p.resolve()) not in skip and str(p) not in skip
            ]

        return self._launch(files, src)

    def _has_running(self) -> bool:
        """是否有正在运行的任务（含刚启动未结束的）。"""
        with self._lock:
            return any(e is not None and not e.is_set() for e in self._jobs.values())

    def _launch(self, files: list[Path], src: Path) -> ProcessingJob:
        """创建任务并后台启动处理线程；无文件则立即完成。"""
        with SessionLocal() as db:
            job = ProcessingJob(
                status=JOB_RUNNING,
                total_files=len(files),
                processed_files=0,
                started_at=datetime.now(),
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id

        cancel_event = threading.Event()
        with self._lock:
            self._jobs[job_id] = cancel_event

        if files:
            threading.Thread(
                target=self._run_job, args=(job_id, src, files, cancel_event), daemon=True
            ).start()
        else:
            self._finish_job(job_id, cancel_event)

        logger.info("批量整理任务 #%s 启动，共 %d 个文件", job_id, len(files))
        return job

    def retry_failed(self) -> ProcessingJob:
        """重新处理收件箱中失败过的文件（复用原记录，不重复建档）。

        供前端「重试失败」入口使用；普通「开始整理」也会自动重试失败项
        （failed 不在跳过集合中），本方法仅把范围限定在失败文件上。
        """
        if self._has_running():
            raise RuntimeError("已有整理任务正在运行，请等待其完成后再重试")
        src = Path(settings.inbox_root).resolve()
        files: list[Path] = []
        if src.exists():
            with SessionLocal() as db:
                rows = db.query(Document.original_path).filter(
                    Document.status == STATUS_FAILED
                ).all()
            for (p,) in rows:
                if not p:
                    continue
                fp = Path(p)
                if fs_isfile(fp):
                    files.append(fp)
        if not files:
            return self._launch([], src)
        logger.info("重试失败文件 %d 个", len(files))
        return self._launch(files, src)

    def cancel_job(self, job_id: int) -> bool:
        """请求取消任务。"""
        with self._lock:
            event = self._jobs.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    # ---------- 内部实现 ----------
    def _run_job(self, job_id: int, src: Path, files: list[Path], cancel_event: threading.Event) -> None:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._process_one, job_id, file_path, cancel_event): file_path
                for file_path in files
            }
            try:
                for future in as_completed(futures):
                    if cancel_event.is_set():
                        # 取消：不再等待剩余任务
                        for f in futures:
                            f.cancel()
                        break
                    future.result()  # 触发异常
            except Exception as e:  # noqa: BLE001
                logger.error("批量任务 #%s 异常: %s", job_id, e)
            finally:
                # 无论是否取消都必须落终态：_finish_job 内部按 cancel_event
                # 判定 CANCELLED / COMPLETED。此前取消时跳过这里，任务会
                # 永远停在 running，前端轮询不到终态而一直转圈。
                self._finish_job(job_id, cancel_event)

    def _finish_job(self, job_id: int, cancel_event: threading.Event) -> None:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job:
                job.status = JOB_CANCELLED if cancel_event.is_set() else JOB_COMPLETED
                job.finished_at = datetime.now()
                db.commit()
        with self._lock:
            self._jobs.pop(job_id, None)
        logger.info("批量任务 #%s 结束: %s", job_id, "已取消" if cancel_event.is_set() else "完成")

    def _process_one(self, job_id: int, file_path: Path, cancel_event: threading.Event) -> None:
        """处理单个文件：登记 → 识别 → 决策。

        每个文件只做一次计数提交（processed + 结果类别合并），
        减少 SQLite 写放大，大目录处理更高效。
        """
        if cancel_event.is_set():
            return
        # outcome 记录本文件最终结果类别，finally 里一次性 bump
        outcome = {"success": False, "review": False, "failed": False, "duplicate": False}
        with SessionLocal() as db:
            # ctx 用于在异常时拿回已登记文档，把状态落为 failed（避免僵尸 pending/processing）
            ctx: dict = {}
            try:
                self._handle_file(db, job_id, file_path, ctx, outcome)
            except Exception as e:  # noqa: BLE001
                logger.error("文件处理失败 %s: %s", file_path, e)
                # 回滚失败事务，否则 session 处于 PendingRollback 状态，
                # 后续任何 commit（含计数更新）都会直接抛错、计数彻底丢失。
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    logger.exception("回滚失败 %s", file_path)
                try:
                    self._mark_failed(db, ctx.get("doc_id"))
                    outcome["failed"] = True
                except Exception:  # noqa: BLE001
                    logger.exception("更新失败计数出错 %s", file_path)
            finally:
                try:
                    self._bump_job(
                        db, job_id, processed=True,
                        success=outcome["success"], review=outcome["review"],
                        failed=outcome["failed"], duplicate=outcome["duplicate"],
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("更新处理计数出错 %s", file_path)

    @staticmethod
    def _mark_failed(db: Session, doc_id: int | None) -> None:
        """把处理中途异常的文档标记为 failed（规格书状态机要求）。"""
        if doc_id is None:
            return
        doc = db.get(Document, doc_id)
        if doc is None or doc.status in (STATUS_ARCHIVED, STATUS_FAILED):
            return
        doc.status = STATUS_FAILED
        db.commit()

    def _handle_file(
        self, db: Session, job_id: int, file_path: Path,
        ctx: dict | None = None, outcome: dict | None = None,
    ) -> None:
        ctx = ctx if ctx is not None else {}
        outcome = outcome if outcome is not None else {}
        file_type = get_file_type(file_path)
        file_hash = sha256_file(file_path)

        # 重复检测（与已归档文档比对）
        # 注意：此处判重只是提前分流省算力，非唯一防线；并发下的真正防线在
        # ArchiveService（判重+移动+落库同一临界区）。
        dup = duplicate_service.find_duplicate(db, file_hash)
        if dup is not None:
            doc = Document(
                original_filename=file_path.name,
                original_path=str(file_path),
                current_path=str(file_path),
                file_type=file_type,
                file_size=file_path.stat().st_size,
                file_hash=file_hash,
                status=STATUS_DUPLICATE,
            )
            db.add(doc)
            db.commit()
            log_operation(db, OP_IMPORT, old_path=str(file_path), new_path=str(file_path),
                          document_id=doc.id, job_id=job_id)
            outcome["duplicate"] = True
            logger.info("重复文件: %s", file_path.name)
            return

        # 复用已登记的记录（如上传接口已写入的 pending），避免同一文件重复建档
        existing = (
            db.query(Document)
            .filter(
                Document.original_path == str(file_path),
                Document.status.in_((STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED)),
            )
            .order_by(Document.id.desc())
            .first()
        )
        if existing is not None:
            doc = existing
            doc.file_type = file_type
            doc.file_hash = file_hash
        else:
            doc = Document(
                original_filename=file_path.name,
                original_path=str(file_path),
                current_path=str(file_path),
                file_type=file_type,
                mime_type=get_mime_type(file_type),
                file_size=file_path.stat().st_size,
                file_hash=file_hash,
                status=STATUS_PROCESSING,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            log_operation(db, OP_IMPORT, old_path=str(file_path), new_path=str(file_path),
                          document_id=doc.id, job_id=job_id)
        doc.status = STATUS_PROCESSING
        db.commit()
        ctx["doc_id"] = doc.id  # 供异常时标记 failed 使用

        # 智能识别
        classifier = ClassifierService(db)
        result = classifier.analyze(file_path, document_id=doc.id)
        log_operation(db, OP_CLASSIFY, old_path=str(file_path), new_path=str(file_path),
                      document_id=doc.id, job_id=job_id)

        # 保存识别结果
        doc.document_type = result.document_type or ""
        doc.title = result.title or ""
        doc.confidence = result.confidence.score if result.confidence else None
        doc.extracted_text = result.text[:5000]
        self._save_fields(db, doc, result)

        if result.error or result.decision == "reject":
            # 无法判断 → 人工审核
            doc.status = STATUS_NEED_REVIEW
            db.commit()
            outcome["review"] = True
            return

        if result.decision == "review":
            doc.status = STATUS_NEED_REVIEW
            db.commit()
            outcome["review"] = True
            return

        # auto → 直接归档
        archive = ArchiveService(db)
        ar = archive.archive(
            file_path,
            category_path=result.suggested_category or "其他",
            filename=result.suggested_filename,
            date_str=result.field_values.get("date"),
            document_id=doc.id,
            job_id=job_id,
        )
        if ar.success:
            # archive_service 已把 doc 标记为 archived
            outcome["success"] = True
        else:
            if ar.duplicate:
                doc.status = STATUS_DUPLICATE
                db.commit()
                outcome["duplicate"] = True
            else:
                doc.status = STATUS_NEED_REVIEW
                db.commit()
                outcome["review"] = True

    def _save_fields(self, db: Session, doc: Document, result) -> None:
        """保存识别字段（含建议分类）。"""
        for name, (value, conf, source) in result.fields.items():
            db.add(DocumentField(
                document_id=doc.id, field_name=name, field_value=str(value),
                confidence=conf, source=source,
            ))
        # 建议分类 path
        db.add(DocumentField(
            document_id=doc.id, field_name="suggested_category",
            field_value=result.suggested_category or "其他",
            confidence=result.confidence.score if result.confidence else 0.0,
            source=SOURCE_RULE,
        ))

    def _bump_job(self, db: Session, job_id: int, *,
                  processed: bool = False, success: bool = False, review: bool = False,
                  failed: bool = False, duplicate: bool = False) -> None:
        """更新任务计数。

        必须用 SQL 侧原子自增：多线程下"读属性 → +1 → commit"会互相覆盖，
        导致 processed_files 少计、前端进度条卡在不到 100% 的位置。
        """
        cols: list[str] = []
        if processed:
            cols.append("processed_files")
        if success:
            cols.append("success_count")
        if review:
            cols.append("review_count")
        if failed:
            cols.append("failed_count")
        if duplicate:
            cols.append("duplicate_count")
        if not cols:
            return
        # UPDATE ... SET x = x + 1 由数据库保证原子性
        column_map = {
            "processed_files": ProcessingJob.processed_files,
            "success_count": ProcessingJob.success_count,
            "review_count": ProcessingJob.review_count,
            "failed_count": ProcessingJob.failed_count,
            "duplicate_count": ProcessingJob.duplicate_count,
        }
        db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(**{col: column_map[col] + 1 for col in cols})
        )
        db.commit()


processing_service = ProcessingService()
