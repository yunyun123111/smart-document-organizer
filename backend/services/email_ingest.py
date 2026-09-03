"""邮箱接收服务：轮询 IMAP 邮箱，把邮件附件存进 inbox 并触发自动识别归档。

工作流：
  手机发邮件（附件=文件）到专用邮箱
    -> 本服务按 EMAIL_POLL_INTERVAL 间隔轮询（IMAP）
    -> 下载附件到 inbox（清洗文件名/防重名/去重）
    -> 登记 Document(pending)
    -> 调用 processing_service.start_job() 自动识别归档

线程生命周期：通过 start_polling / stop_polling 动态启停（配置保存后立即生效，
无需重启系统）。最近一次收取结果（成功数/错误信息）由 get_status() 暴露给界面。
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import json
import re
import threading
import time
from datetime import datetime
from email.header import decode_header
from pathlib import Path

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Document, STATUS_PENDING
from backend.utils.file_utils import get_file_type, get_mime_type
from backend.utils.filename_utils import safe_filename, unique_filename
from backend.utils.logger import get_logger

logger = get_logger("services.email_ingest")

# 与网页上传保持一致的支持类型
SUPPORTED_EXTS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
}
_SKIP_NAME_RE = re.compile(r"winmail\.dat$", re.IGNORECASE)

# ---- 最近一次收取状态（供界面展示真实结果/错误） ----
_status_lock = threading.Lock()
_status: dict = {"last_check": None, "last_error": None, "last_count": 0, "running": False}

# ---- 轮询线程管理 ----
_poll_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None


def _decoded(value: str | None) -> str:
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


def _send_imap_id(m: imaplib.IMAP4) -> None:
    """登录后发送 IMAP ID 命令（RFC 2971）。

    163/188 邮箱的 "SELECT Unsafe Login" 正是缺少该信息导致。
    imaplib 的 Commands 表不包含 ID，_simple_command 会直接 KeyError，
    因此这里手动构造原始命令字节并发送（绕过 Commands 检查）。
    其他邮箱不支持时静默忽略。
    """
    try:
        tag = m._new_tag()
        params = (
            b'"name" "SmartDocumentOrganizer" '
            b'"version" "1.0.0" '
            b'"vendor" "smart-document-organizer" '
            b'"support-email" "support@smartdoc.local"'
        )
        m.send(tag + b" ID (" + params + b")\r\n")
        typ, data = m._get_tagged_response(tag)
        if typ == "OK":
            logger.info("IMAP ID 信息已发送（RFC 2971）")
        else:
            logger.warning("IMAP ID 服务器未接受: %s %s", typ, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("IMAP ID 命令发送失败（忽略，继续）: %s", e)


def _connect() -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    cls = imaplib.IMAP4_SSL if settings.EMAIL_SSL else imaplib.IMAP4
    m = cls(settings.EMAIL_IMAP_HOST, settings.EMAIL_IMAP_PORT)
    m.login(settings.EMAIL_USER, settings.EMAIL_PASSWORD)
    # 网易要求登录后、SELECT 前带上 IMAP ID 信息
    _send_imap_id(m)
    typ, data = m.select("INBOX")
    if typ != "OK":
        # 把服务端原始错误透出，便于定位（如 163 的 Unsafe Login）
        msg = b" ".join(data).decode("utf-8", "ignore") if data else "SELECT failed"
        m.logout()
        raise RuntimeError(f"IMAP 选择收件箱失败：{msg}")
    return m


def _save_attachments(msg) -> list[Path]:
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
        # 统一安全命名：safe_filename 处理非法字符/保留字/长度，unique_filename 防重名
        target = unique_filename(inbox, safe_filename(Path(filename).stem or "邮件附件", ext))
        try:
            target.write_bytes(payload)
            saved.append(target)
            logger.info("邮件附件已保存: %s", target.name)
        except OSError:
            logger.exception("保存附件失败: %s", filename)
    return saved


def _register_and_process(files: list[Path]) -> int:
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


def _set_status(**kwargs) -> None:
    with _status_lock:
        _status.update(kwargs)


def poll_once() -> int:
    """拉取一次邮件附件。返回新增文件数（0 表示无/失败）。

    结果与错误写入 _status，供界面展示。
    """
    if not settings.EMAIL_ENABLED or not settings.EMAIL_IMAP_HOST or not settings.EMAIL_USER:
        _set_status(last_check=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    last_error="邮箱未启用或未配置", last_count=0)
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
            _set_status(last_check=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        last_error="搜索邮件失败", last_count=0)
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
        try:
            m.store("1:*", "+FLAGS", "\\Seen")
        except Exception:
            pass
        m.logout()
    except Exception as e:
        logger.exception("邮箱轮询失败")
        _set_status(last_check=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    last_error=str(e)[:300], last_count=0)
        return 0
    _save_processed(processed)
    if new_files:
        added = _register_and_process(new_files)
        _set_status(last_check=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    last_error=None, last_count=added)
        return added
    _set_status(last_check=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                last_error=None, last_count=0)
    return 0


# ---- 轮询线程生命周期（配置保存后动态启停，无需重启） ----
def _poll_loop(interval: int, stop_event: threading.Event) -> None:
    logger.info("邮箱轮询线程开始 (interval=%ss)", interval)
    _set_status(running=True)
    try:
        while not stop_event.is_set():
            try:
                count = poll_once()
                if count:
                    logger.info("邮箱接收本轮新增 %s 个文件", count)
            except Exception:
                logger.exception("邮箱轮询异常")
            stop_event.wait(max(30, int(interval) or 120))
    finally:
        _set_status(running=False)
        logger.info("邮箱轮询线程停止")


def start_polling(interval: int | None = None) -> bool:
    """启动轮询线程（若已在跑则忽略）。"""
    global _poll_thread, _stop_event
    if _poll_thread and _poll_thread.is_alive():
        return True
    _stop_event = threading.Event()
    _poll_thread = threading.Thread(
        target=_poll_loop, args=(interval or settings.EMAIL_POLL_INTERVAL, _stop_event),
        name="email-ingest", daemon=True,
    )
    _poll_thread.start()
    return True


def stop_polling() -> None:
    global _poll_thread, _stop_event
    if _stop_event:
        _stop_event.set()
    _poll_thread = None
    _stop_event = None


def sync_from_settings() -> bool:
    """根据当前 settings 启停轮询。返回是否在运行。"""
    if settings.EMAIL_ENABLED and settings.EMAIL_IMAP_HOST and settings.EMAIL_USER:
        start_polling()
        return True
    stop_polling()
    return False


def get_status() -> dict:
    with _status_lock:
        s = dict(_status)
    s["running"] = bool(_poll_thread and _poll_thread.is_alive())
    s["enabled"] = settings.EMAIL_ENABLED
    return s
