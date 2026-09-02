# -*- coding: utf-8 -*-
"""P0-3: 一键备份 / 恢复服务。

- 备份：SQLite 一致性副本（backup API）+ .env 配置 + 可选归档文档 → zip
- 恢复：校验备份包 → 导入数据库副本 + 解压文档 + 重跑迁移
- 备份存放在 data/backups/，设置页提供创建 / 下载 / 删除 / 恢复
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from backend.config import BASE_DIR, settings
from backend.database import SessionLocal, run_migrations
from backend.utils.logger import get_logger

logger = get_logger("services.backup")

if settings.DATABASE_URL.startswith("sqlite:///"):
    # sqlite:///C:/.../app.db -> C:/.../app.db
    _db_file = Path(settings.DATABASE_URL.replace("sqlite:///", ""))
else:
    _db_file = BASE_DIR / "data/database/app.db"

META_FILENAME = "meta.json"
DB_ARCNAME = "app.db"
ENV_ARCNAME = ".env"
DOC_ARCNAME = "documents"


def _db_path() -> Path:
    return _db_file


def _backup_path() -> Path:
    return _db_file.parent / "backups"


def _build_meta(include_documents: bool) -> dict:
    with sqlite3.connect(str(_db_path())) as conn:
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        try:
            log_count = conn.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0]
        except sqlite3.OperationalError:
            log_count = 0
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 1,
        "documents_count": doc_count,
        "operation_logs_count": log_count,
        "include_documents": include_documents,
        "app": settings.APP_NAME,
    }


def create_backup(include_documents: bool = False) -> dict:
    """创建备份 zip，返回备份信息。"""
    settings.ensure_dirs()
    bdir = _backup_path()
    bdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{ts}.zip"
    target = bdir / filename

    # 1) 数据库一致性副本（backup API，含 WAL 中未落盘事务）
    tmp_db = bdir / f"_tmp_{ts}.db"
    try:
        with sqlite3.connect(str(_db_path())) as src:
            dst = sqlite3.connect(str(tmp_db))
            try:
                src.backup(dst)
            finally:
                dst.close()

        # 2) 打包
        meta = _build_meta(include_documents)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, DB_ARCNAME)
            env_file = BASE_DIR / ".env"
            if env_file.exists():
                zf.write(env_file, ENV_ARCNAME)
            if include_documents and settings.document_root.exists():
                base = settings.document_root
                for p in sorted(base.rglob("*")):
                    if p.is_file():
                        zf.write(p, f"{DOC_ARCNAME}/{p.relative_to(base).as_posix()}")
            zf.writestr(META_FILENAME, json.dumps(meta, ensure_ascii=False, indent=2))

        size = target.stat().st_size
        logger.info("备份完成: %s (%.1f KB, 文档=%s)", filename, size / 1024, include_documents)
        return {
            "filename": filename,
            "size": size,
            "created_at": meta["created_at"],
            "include_documents": include_documents,
            "documents_count": meta["documents_count"],
        }
    finally:
        if tmp_db.exists():
            tmp_db.unlink()


def list_backups() -> list[dict]:
    bdir = _backup_path()
    if not bdir.exists():
        return []
    result = []
    for f in sorted(bdir.glob("backup_*.zip"), reverse=True):
        try:
            with zipfile.ZipFile(f) as zf:
                meta_raw = zf.read(META_FILENAME).decode("utf-8")
                meta = json.loads(meta_raw)
        except Exception:  # noqa: BLE001
            meta = {}
        result.append(
            {
                "filename": f.name,
                "size": f.stat().st_size,
                "created_at": meta.get("created_at") or datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "documents_count": meta.get("documents_count", 0),
                "include_documents": meta.get("include_documents", False),
            }
        )
    return result


def delete_backup(filename: str) -> bool:
    """删除指定备份文件（防路径穿越）。"""
    name = Path(filename).name
    if not name.startswith("backup_") or not name.endswith(".zip"):
        return False
    target = _backup_path() / name
    if target.exists():
        target.unlink()
        logger.info("已删除备份: %s", name)
        return True
    return False


def get_backup_path(filename: str) -> Path | None:
    name = Path(filename).name
    if not name.startswith("backup_") or not name.endswith(".zip"):
        return None
    target = _backup_path() / name
    return target if target.exists() else None


def restore_backup(zip_path: Path) -> dict:
    """从备份 zip 恢复：导入数据库 + 解压文档 + 重跑迁移。

    高风险操作：会覆盖当前数据库。调用方需确认。
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"备份文件不存在: {zip_path}")

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if DB_ARCNAME not in names:
            raise ValueError("备份包缺少 app.db，无法恢复")

        # 1) 数据库：用 backup API 从备份副本导入当前库
        tmp_extract = _backup_path() / f"_restore_{datetime.now().strftime('%H%M%S')}.db"
        try:
            with zf.open(DB_ARCNAME) as src_f, open(tmp_extract, "wb") as out_f:
                shutil.copyfileobj(src_f, out_f)
            # 显式 open/close：Windows 上 with 隐式关闭可能延迟释放句柄
            src = sqlite3.connect(str(tmp_extract))
            dst = sqlite3.connect(str(_db_path()))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

            # 2) 文档：解压到 DOCUMENT_ROOT
            restored_docs = 0
            prefix = f"{DOC_ARCNAME}/"
            for name in names:
                if name.startswith(prefix) and not name.endswith("/"):
                    rel = name[len(prefix):]
                    dest = settings.document_root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src_f, open(dest, "wb") as out_f:
                        shutil.copyfileobj(src_f, out_f)
                    restored_docs += 1

            # 3) 配置（.env）存在则恢复——注意：恢复 .env 会覆盖当前运行配置
            if ENV_ARCNAME in names:
                env_file = BASE_DIR / ".env"
                with zf.open(ENV_ARCNAME) as src_f, open(env_file, "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f)

            # 4) 重跑迁移（备份可能来自旧版本）
            with SessionLocal() as db:
                run_migrations(db)

            logger.info("恢复完成: %s（文档 %d 份）", zip_path.name, restored_docs)
            return {
                "ok": True,
                "restored_documents": restored_docs,
                "database": _db_path().name,
            }
        finally:
            if tmp_extract.exists():
                # Windows 下文件句柄可能延迟释放，尝试删除并退避
                for _ in range(5):
                    try:
                        tmp_extract.unlink()
                        break
                    except PermissionError:
                        import time

                        time.sleep(0.2)
