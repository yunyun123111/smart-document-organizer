"""邮箱接收服务：轮询 IMAP 邮箱，把邮件附件存进 inbox 并触发自动识别归档。

工作流：
  手机发邮件（附件=文件）到专用邮箱
    -> 本服务按 EMAIL_POLL_INTERVAL 间隔轮询（IMAP）
    -> 下载附件到 inbox（清洗文件名/防重名/去重）
    -> 登记 Document(pending)
    -> 调用 processing_service.start_job() 自动识别归档
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import json
import re
import threading
import time
from email.header import decode_header
from pathlib import Path

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Document, STATUS_PENDING
from backend.utils.file_utils import get_file_type, get_mime_type
from backend.utils.filename_utils import unique_filename
from backend.utils.logger import get_logger

logger = get_logger("services.email_ingest")

# 与网页上传保持一致的支持类型
SUPPORTED_EXTS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
}
# 排除常见的非业务附件
_SKIP_NAME_RE = re.compile(r"winmail\.dat$", re.IGNORECASE)


def _decoded(value: str | None) -> str:
    """解码邮件头/文件名（支持 RFC2047 编码）。"""
    if not value:
        return ""
    try:
        parts = decode_header(value)
    except Exception:
        return value
    out: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            try:
                out.append(data.decode(charset or "utf-8", "ignore"))
            except (LookupError, TypeError):
                out.append(data.decode("utf-8", "ignore"))
        else:
            out.append(data)
    return "".join(out)


def _processed_path() -> Path:
    """已处理邮件 Message-ID 记录文件（用于去重，避免重复下载）。"""
    return Path(settings.TEMP_DIR) / "email_processed.json"


def _load_processed() -> set[str]:
    p = _processed_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_processed(ids: set[str]) -> None:
    try:
        _processed_path().parent.mkdir(parents=True, exist_ok=True)
        _processed_path().write_text(
            json.dumps(sorted(ids), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:
        logger.exception("保存已处理邮件记录失败")


def _connect() -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    cls = imaplib.IMAP4_SSL if settings.EMAIL_SSL else imaplib.IMAP4
    m = cls(settings.EMAIL_IMAP_HOST, settings.EMAIL_IMAP_PORT)
    m.login(settings.EMAIL_USER, settings.EMAIL_PASSWORD)
    m.select("INBOX")
    return m


def _save_attachments(msg) -> list[Path]:
    """把邮件附件保存到 inbox，返回保存路径列表。"""
    saved: list[Path] = []
    inbox = Path(settings.INBOX_ROOT)
    inbox.mkdir(parents=True, exist_ok=True)
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decoded(part.get_filename())
        if not filename or _SKIP_NAME_RE.search(filename):
            continue
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            logger.info("跳过不支持的附件类型: %s", filename)
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        # 清洗文件名 + 防重名（保持原名，仅清理非法字符）
        base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", Path(filename).stem).strip(" .")
        if not base:
            base = "邮件附件"
        target = unique_filename(inbox, f"{base}{ext}")
        try:
            target.write_bytes(payload)
            saved.append(target)
            logger.info("邮件附件已保存: %s", target.name)
        except OSError:
            logger.exception("保存附件失败: %s", filename)
    return saved


def _register_and_process(files: list[Path]) -> int:
    """登记 Document(pending) 并触发自动整理。返回登记数量。"""
    from backend.services.processing_service import processing_service
    from backend.services.operation_service import log_operation

    added = 0
    with SessionLocal() as db:
        for target in files:
            file_type = get_file_type(target)
            if file_type == "unknown":
                logger.warning("不支持的邮箱附件类型，删除: %s", target.name)
                target.unlink(missing_ok=True)
                continue
            file_hash = _sha256_file(target)
            existing = db.query(Document).filter(Document.file_hash == file_hash).first()
            if existing is not None:
                logger.info("邮箱附件与已有文档重复，删除: %s", target.name)
                target.unlink(missing_ok=True)
                continue
            doc = Document(
                original_filename=target.name,
                original_path=str(target),
                current_path=str(target),
                file_type=file_type,
                mime_type=get_mime_type(file_type),
                file_size=target.stat().st_size,
                file_hash=file_hash,
                status=STATUS_PENDING,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            log_operation(
                db, "IMPORT", old_path=str(target), new_path=str(target),
                document_id=doc.id, result="ok", error_message="邮箱接收",
            )
            added += 1
            logger.info("邮箱附件登记: %s (doc#%s)", target.name, doc.id)
    if added:
        try:
            job = processing_service.start_job()
            logger.info("邮箱接收触发自动整理 job#%s (%s 个新文件)", job.id, added)
        except Exception:
            logger.exception("邮箱接收触发自动整理失败")
    return added


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def poll_once() -> int:
    """拉取一次邮件附件。返回新增文件数（0 表示无/失败）。"""
    if not settings.EMAIL_ENABLED or not settings.EMAIL_IMAP_HOST or not settings.EMAIL_USER:
        return 0
    processed = _load_processed()
    new_files: list[Path] = []
    try:
        m = _connect()
        try:
            typ, data = m.search(None, "UNSEEN")
        except imaplib.IMAP4.error:
            typ, data = m.search(None, "ALL")
        if typ != "OK":
            m.logout()
            return 0
        for num in data[0].split():
            typ, msg_data = m.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            try:
                msg = email.message_from_bytes(msg_data[0][1])
            except Exception:
                continue
            msg_id = _decoded(msg.get("Message-ID")) or f"UID-{num.decode()}"
            if msg_id in processed:
                continue
            got = _save_attachments(msg)
            new_files.extend(got)
            processed.add(msg_id)
        # 标记本轮已读（避免重复抓取）
        try:
            m.store("1:*", "+FLAGS", "\\Seen")
        except Exception:
            pass
        m.logout()
    except Exception:
        logger.exception("邮箱轮询失败")
        return 0
    _save_processed(processed)
    if new_files:
        added = _register_and_process(new_files)
        return added
    return 0


def email_poll_loop(interval: int) -> threading.Thread:
    """启动后台轮询线程（daemon）。"""
    def _run():
        logger.info("邮箱轮询线程开始 (interval=%ss)", interval)
        while True:
            try:
                count = poll_once()
                if count:
                    logger.info("邮箱接收本轮新增 %s 个文件", count)
            except Exception:
                logger.exception("邮箱轮询异常")
            time.sleep(max(30, int(interval) or 120))

    t = threading.Thread(target=_run, name="email-ingest", daemon=True)
    t.start()
    return t
