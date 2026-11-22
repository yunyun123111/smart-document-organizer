"""文件重定位服务：检测路径失效的记录，并在指定目录下按文件名/哈希重新关联。

场景：用户把已归档文件移动到其他文件夹后，数据库 current_path 失效，
通过本服务在归档根目录（或用户指定的新目录）中找回文件并更新路径。
"""
from __future__ import annotations

from pathlib import Path

from backend.config import BASE_DIR, settings
from backend.models import Document
from backend.utils.hash_utils import sha256_file
from backend.utils.logger import get_logger

logger = get_logger("services.relocate")


def missing_documents(db) -> list[dict]:
    """返回当前路径失效（文件不存在）的记录。"""
    out: list[dict] = []
    rows = (
        db.query(Document.id, Document.original_filename,
                 Document.current_filename, Document.current_path)
        .filter(Document.status != "recycled")  # 回收站文件不参与关联找回
        .all()
    )
    for doc_id, original, current, path in rows:
        raw = Path(path or "")
        # 相对路径一律视为失效：历史版本重定位曾写入 ./data/... 相对路径，
        # 换一个工作目录启动就找不到文件，必须重新解析成绝对路径。
        abs_p = raw if raw.is_absolute() else (BASE_DIR / raw)
        if (not raw.is_absolute()) or (not abs_p.exists()):
            out.append({
                "id": doc_id,
                "original_filename": original,
                "current_filename": current,
                "path": str(abs_p),
            })
    return out


def _collect_files(roots: list[Path]) -> dict[str, list[Path]]:
    """递归扫描目录，建立 文件名 -> 候选路径 索引。"""
    index: dict[str, list[Path]] = {}
    seen_dirs: set[Path] = set()
    for root in roots:
        if not root.is_dir() or root in seen_dirs:
            continue
        seen_dirs.add(root)
        try:
            for p in root.rglob("*"):
                if p.is_file():
                    index.setdefault(p.name, []).append(p)
        except OSError as e:
            logger.warning("扫描目录失败 %s: %s", root, e)
    return index


def relocate(
    db,
    search_roots: list[str] | None = None,
    by_hash: bool = False,
) -> dict:
    """重新关联失效文件。

    - search_roots: 搜索目录（默认归档根 data/documents；可传用户指定的新目录）
    - by_hash: 是否按文件哈希精确匹配（更准，但需读取文件内容，较慢）
    返回统计结果。
    """
    missing = missing_documents(db)
    if not missing:
        return {"relinked": 0, "failed": 0, "total": 0, "matched": [], "unmatched": []}

    roots: list[Path] = []
    # 默认同时搜索归档目录与收件箱：迁移到新机器后，未归档文档仍留在 inbox
    for default_root in (settings.document_root, settings.inbox_root):
        if default_root not in roots:
            roots.append(default_root)
    for r in search_roots or []:
        rp = Path(r)
        if not rp.is_absolute():
            rp = (BASE_DIR / rp)
        if rp.is_dir():
            roots.append(rp)
        else:
            logger.warning("搜索目录不存在: %s", r)

    index = _collect_files(roots)
    if not index:
        return {
            "relinked": 0, "failed": len(missing), "total": len(missing),
            "matched": [], "unmatched": [m for m in missing],
            "error": "搜索目录中未找到任何文件",
        }

    matched: list[dict] = []
    unmatched: list[dict] = []
    relinked = 0
    for m in missing:
        doc = db.get(Document, m["id"])
        if not doc:
            unmatched.append(m)
            continue
        # 候选：当前文件名 -> 原始文件名 -> 原路径的文件名（迁移/改名后仍保留 disk 名）
        candidates: list[Path] = []
        for name in (m["current_filename"], m["original_filename"], Path(m["path"]).name):
            if not name:
                continue
            for p in index.get(name, []):
                if p not in candidates:
                    candidates.append(p)
        chosen: Path | None = None
        if by_hash and candidates:
            target_hash = doc.file_hash
            for p in candidates:
                try:
                    if sha256_file(p) == target_hash:
                        chosen = p
                        break
                except OSError:
                    continue
        elif candidates:
            chosen = candidates[0]
        if chosen is not None:
            doc.current_path = str(chosen)
            matched.append({"id": m["id"], "filename": m["current_filename"],
                            "new_path": str(chosen)})
            relinked += 1
            logger.info("重新关联文件 #%s -> %s", m["id"], chosen)
        else:
            unmatched.append(m)

    db.commit()
    return {
        "relinked": relinked,
        "failed": len(unmatched),
        "total": len(missing),
        "matched": matched,
        "unmatched": unmatched,
    }
