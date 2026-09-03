"""文档库 API：列表 / 详情 / 上传 / 删除 / 预览。"""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from backend.config import settings
from backend.database import get_db
from backend.models import (
    OP_DELETE,
    OP_RENAME,
    OP_USER_EDIT,
    RESULT_OK,
    SOURCE_USER,
    STATUS_NEED_REVIEW,
    STATUS_PROCESSING,
    STATUS_RECYCLED,
    Document,
    DocumentField,
)
from backend.schemas.document import DocumentDetail, DocumentListItem
from backend.schemas.processing import UploadResponse
from backend.services.document_search import (
    build_keyword_conditions,
    match_field_ids,
    suggest,
)
from backend.services.duplicate_service import duplicate_service
from backend.services.relocate_service import missing_documents, relocate
from backend.services.operation_service import log_operation
from backend.services.recycle_service import recycle_service
from backend.services.processing_service import processing_service
from backend.utils.file_utils import get_file_type, get_mime_type
from backend.utils.filename_utils import safe_filename, unique_filename
from backend.utils.fs_path import fs_exists, fs_path
from backend.utils.hash_utils import sha256_file
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/documents", tags=["documents"])

_AMOUNT_FIELDS = ("amount", "total_amount", "tax_amount", "price")
_DATE_FIELDS = ("date", "invoice_date", "ship_date", "bill_date")
logger = get_logger("api.documents")


def _to_list_item(doc: Document) -> DocumentListItem:
    return DocumentListItem(
        id=doc.id,
        original_filename=doc.original_filename,
        current_filename=doc.current_filename,
        file_type=doc.file_type,
        file_size=doc.file_size,
        document_type=doc.document_type,
        title=doc.title,
        confidence=doc.confidence,
        status=doc.status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("", response_model=list[DocumentListItem])
def list_documents(
    status: str | None = None,
    document_type: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    contract_no: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """文档列表：状态/类型/分类/关键词（含拼音与错别字容错）+ 合同号/金额区间/日期范围。"""
    # 回收站文件不进入正常文档库（回收站走独立 /api/recycle-bin 页面）
    query = db.query(Document).filter(Document.status != STATUS_RECYCLED).order_by(Document.created_at.desc())
    if status:
        query = query.filter(Document.status == status)
    if document_type:
        if document_type == "未识别":
            query = query.filter(Document.document_type.in_([None, ""]))
        else:
            query = query.filter(Document.document_type.like(f"%{document_type}%"))
    if category:
        sub = (
            db.query(DocumentField.document_id)
            .filter(
                DocumentField.field_name == "suggested_category",
                DocumentField.field_value.like(f"%{category}%"),
            )
        )
        query = query.filter(Document.id.in_(sub))
    if keyword and keyword.strip():
        conds = build_keyword_conditions(db, keyword.strip())
        query = query.filter(or_(*conds))
    # ---- 合同号模糊过滤 ----
    if contract_no:
        ids = match_field_ids(
            db, ("contract_no", "contract_number", "invoice_no"),
            like=contract_no.strip(),
        )
        if ids:
            query = query.filter(Document.id.in_(ids))
        else:
            return []
    # ---- 金额区间过滤 ----
    if amount_min is not None or amount_max is not None:
        ids = match_field_ids(
            db, _AMOUNT_FIELDS,
            min_val=amount_min, max_val=amount_max,
        )
        if ids:
            query = query.filter(Document.id.in_(ids))
        else:
            return []
    # ---- 日期范围过滤（date 字段 或 created_at）----
    if date_start or date_end:
        conds = []
        field_ids = match_field_ids(
            db, _DATE_FIELDS, start=date_start, end=date_end,
        )
        if field_ids:
            conds.append(Document.id.in_(field_ids))
        if date_start:
            conds.append(Document.created_at >= date_start)
        if date_end:
            conds.append(Document.created_at <= f"{date_end} 23:59:59")
        query = query.filter(or_(*conds))
    docs = query.offset(skip).limit(limit).all()

    # 重复文件：附加「重复来源」（同 hash 最早的非重复记录的文件名）
    dup_map: dict[str, str] = {}
    dup_docs = [d for d in docs if d.status == "duplicate" and d.file_hash]
    if dup_docs:
        hashes = {d.file_hash for d in dup_docs}
        originals = (
            db.query(Document)
            .filter(
                Document.file_hash.in_(hashes),
                Document.status != "duplicate",
            )
            .order_by(Document.id.asc())
            .all()
        )
        for o in originals:
            dup_map.setdefault(
                o.file_hash, o.original_filename or o.current_filename or f"文档#{o.id}"
            )

    return [
        DocumentListItem(
            id=d.id,
            original_filename=d.original_filename,
            current_filename=d.current_filename,
            file_type=d.file_type,
            file_size=d.file_size,
            document_type=d.document_type,
            title=d.title,
            confidence=d.confidence,
            status=d.status,
            duplicate_of=dup_map.get(d.file_hash) if d.status == "duplicate" else None,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in docs
    ]


class RelocateRequest(BaseModel):
    search_roots: list[str] | None = None
    by_hash: bool = False


@router.get("/relocate/status")
def relocate_status(db: Session = Depends(get_db)):
    """检测路径失效（文件已被移动/删除）的记录。"""
    missing = missing_documents(db)
    total = db.query(Document).count()
    return {"total": total, "missing_count": len(missing), "missing": missing}


@router.post("/relocate")
def relocate_documents(req: RelocateRequest, db: Session = Depends(get_db)):
    """重新关联失效文件：在归档根/指定目录下按文件名或哈希找回并更新路径。"""
    return relocate(db, search_roots=req.search_roots, by_hash=req.by_hash)


@router.get("/suggest")
def suggest_documents(keyword: str = "", db: Session = Depends(get_db)):
    """搜索建议：类型 / 公司 / 合同号 / 发票号 / 船名 / 物料 / 文件名。"""
    return {"suggestions": suggest(db, keyword)}


@router.get("/types")
def list_document_types(db: Session = Depends(get_db)):
    """返回全部文档类型（供筛选下拉，排除回收站）。"""
    rows = (
        db.query(Document.document_type)
        .filter(Document.status != STATUS_RECYCLED)
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


@router.get("/categories")
def list_document_categories(db: Session = Depends(get_db)):
    """返回全部归档分类路径（供筛选下拉，排除回收站）。"""
    rows = (
        db.query(DocumentField.field_value)
        .join(Document, Document.id == DocumentField.document_id)
        .filter(
            DocumentField.field_name == "suggested_category",
            Document.status != STATUS_RECYCLED,
        )
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


class BatchDeleteRequest(BaseModel):
    doc_ids: list[int]


@router.post("/batch-delete")
def batch_delete_documents(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    """批量删除文档 -> 批量移入回收站（不再物理删除，可恢复）。"""
    deleted: list[int] = []
    missing: list[int] = []
    recycled_count = 0
    failed_count = 0
    errors: list[str] = []
    for doc_id in req.doc_ids:
        doc = db.get(Document, doc_id)
        if not doc:
            missing.append(doc_id)
            continue
        if doc.status == STATUS_RECYCLED:
            continue  # 已在回收站，跳过
        res = recycle_service.move_to_recycle(db, doc, reason="批量删除")
        if res.success:
            deleted.append(doc_id)
            if res.file_moved:
                recycled_count += 1
        else:
            failed_count += 1
            errors.append(f"#{doc_id}: {res.error}")
    logger.info(
        "批量移入回收站: %d 条（缺失 %d，失败 %d）", len(deleted), len(missing), failed_count
    )
    return {
        "ok": True,
        "deleted_count": len(deleted),
        "missing_count": len(missing),
        "recycled_count": recycled_count,
        "failed_count": failed_count,
        "errors": errors,
    }


class ExportZipRequest(BaseModel):
    doc_ids: list[int]
    zip_name: str = "documents"


@router.post("/export-zip")
def export_documents_zip(req: ExportZipRequest, db: Session = Depends(get_db)):
    """把选中文档的磁盘文件打包为 zip 下载。"""
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc_id in req.doc_ids:
            doc = db.get(Document, doc_id)
            if not doc:
                continue
            path = Path(doc.current_path or doc.original_path)
            if not path.is_file():
                continue
            zf.write(str(path), arcname=doc.current_filename or path.name)
            written += 1
    if written == 0:
        raise HTTPException(status_code=404, detail="没有可导出的文件")
    buffer.seek(0)
    safe_zip = req.zip_name.replace("\\", "_").replace("/", "_") or "documents"
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_zip}.zip"',
    }
    logger.info("导出压缩包: %s.zip 含 %d 个文件", safe_zip, written)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers=headers,
    )


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).options(joinedload(Document.fields)).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.get("/{doc_id}/file")
def get_document_file(doc_id: int, db: Session = Depends(get_db)):
    """下载/预览原文件。"""
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = Path(doc.current_path)
    if not fs_exists(path):
        raise HTTPException(status_code=404, detail="文件已被移动或删除")
    media_type = doc.mime_type or "application/octet-stream"
    # fs_path：长路径（>260）自动转 \\?\ 前缀，否则 FileResponse 打不开
    return FileResponse(fs_path(path), media_type=media_type, filename=doc.current_filename)


class RenameRequest(BaseModel):
    filename: str


def _do_rename(db: Session, doc: Document, base: str) -> tuple[str, Path, bool]:
    """重命名文档磁盘文件并更新记录。返回 (新文件名, 新路径, 是否变化)。"""
    src = Path(doc.current_path or doc.original_path)
    if not fs_exists(src):
        raise HTTPException(status_code=400, detail="磁盘文件不存在，无法重命名")
    ext = src.suffix
    base = (base or "").strip().strip('"')
    if not base:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    new_name = safe_filename(base, ext)
    if new_name == src.name:
        return src.name, src, False
    target = unique_filename(src.parent, new_name)
    try:
        shutil.move(fs_path(src), fs_path(target))
    except OSError as e:
        logger.error("重命名失败 %s -> %s: %s", src, target, e)
        raise HTTPException(status_code=500, detail=f"文件重命名失败: {e}")
    doc.current_filename = target.name
    doc.current_path = str(target)
    log_operation(
        db,
        OP_RENAME,
        old_path=str(src),
        new_path=str(target),
        document_id=doc.id,
        result=RESULT_OK,
        error_message="手动重命名",
    )
    return target.name, target, True


@router.post("/{doc_id}/rename")
def rename_document(doc_id: int, req: RenameRequest, db: Session = Depends(get_db)):
    """手动重命名文档：移动磁盘文件 + 更新记录 + 审计日志。"""
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    new_name, target, changed = _do_rename(db, doc, req.filename)
    db.commit()
    logger.info("手动重命名: %s -> %s (doc#%s)", target.name, new_name, doc.id)
    return {"ok": True, "filename": target.name, "path": str(target), "changed": changed}


class DocumentPatchRequest(BaseModel):
    """已归档文档信息修正：文档类型 / 标题 / 识别字段 / 归档分类 / 文件名。"""
    document_type: Optional[str] = None
    title: Optional[str] = None
    fields: Optional[dict[str, str]] = None  # 字段名 -> 值（含 suggested_category）
    filename: Optional[str] = None           # 可选：同时重命名磁盘文件


def _upsert_fields(db: Session, doc: Document, fields: dict[str, str]) -> None:
    """把用户提交的字段写入文档档案（存在则更新，不存在则新增）。

    - suggested_category 是唯一字段，必须按字段名查找，不能按值查找；
    - 用户选定的分类也要落库，避免信息修正后分类丢失。
    """
    existing = {f.field_name: f for f in doc.fields}

    def _set(name: str, value: str) -> None:
        if name in existing:
            existing[name].field_value = value
        else:
            db.add(
                DocumentField(
                    document_id=doc.id,
                    field_name=name,
                    field_value=value,
                    confidence=1.0,
                    source=SOURCE_USER,
                )
            )

    for name, value in fields.items():
        if name == "suggested_category":
            continue
        _set(name, str(value))
    if "suggested_category" in fields:
        _set("suggested_category", str(fields["suggested_category"]))


@router.patch("/{doc_id}", response_model=DocumentDetail)
def update_document(doc_id: int, req: DocumentPatchRequest, db: Session = Depends(get_db)):
    """修正已归档文档的信息（文档类型 / 标题 / 识别字段 / 归档分类 / 文件名）。"""
    doc = (
        db.query(Document)
        .options(joinedload(Document.fields))
        .filter(Document.id == doc_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    changed = False
    if req.document_type is not None and req.document_type.strip() != doc.document_type:
        doc.document_type = req.document_type.strip()
        changed = True
    if req.title is not None and req.title.strip() != doc.title:
        doc.title = req.title.strip()
        changed = True
    if req.fields:
        _upsert_fields(db, doc, req.fields)
        changed = True
    if req.filename is not None and req.filename.strip():
        _, _, renamed = _do_rename(db, doc, req.filename)
        changed = changed or renamed
    if changed:
        log_operation(
            db,
            OP_USER_EDIT,
            old_path=doc.original_path or "",
            new_path=doc.current_path or "",
            document_id=doc.id,
            result=RESULT_OK,
            error_message="文档库信息修正（类型/字段/分类/文件名）",
        )
        db.commit()
        db.refresh(doc)
    return doc


@router.delete("/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status == STATUS_RECYCLED:
        raise HTTPException(status_code=400, detail="该文档已在回收站")
    res = recycle_service.move_to_recycle(db, doc, reason="用户删除")
    if not res.success:
        raise HTTPException(status_code=400, detail=res.error)
    logger.info(
        "移入回收站: doc#%s %s (文件%s)",
        doc.id, doc.original_filename, "已移动" if res.file_moved else "缺失",
    )
    return {
        "ok": True,
        "recycled": True,
        "recycle_id": res.recycle_id,
        "file_moved": res.file_moved,
    }


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传文件到 inbox 并登记文档（待整理）。"""
    inbox = settings.inbox_root
    Path(inbox).mkdir(parents=True, exist_ok=True)
    # 统一安全命名：safe_filename 清洗（含路径分隔符/非法字符/保留字/长度），
    # unique_filename 对重名递增 _001/_002…，绝不覆盖已有文件
    fname = file.filename or "上传文件"
    safe_name = safe_filename(Path(fname).stem or "上传文件", Path(fname).suffix)
    content = await file.read()
    target = unique_filename(inbox, safe_name)
    target.write_bytes(content)

    file_type = get_file_type(target)
    if file_type == "unknown":
        target.unlink()
        raise HTTPException(status_code=400, detail="不支持的文件类型")

    file_hash = sha256_file(target)
    # 重复检测：任何同 hash 记录（已归档/待审核/处理中）都视为已提交过
    existing = db.query(Document).filter(Document.file_hash == file_hash).first()
    if existing is not None:
        target.unlink(missing_ok=True)
        return UploadResponse(document_id=existing.id, filename=file.filename, status="duplicate")

    doc = Document(
        original_filename=target.name,
        original_path=str(target),
        current_path=str(target),
        file_type=file_type,
        mime_type=get_mime_type(file_type),
        file_size=target.stat().st_size,
        file_hash=file_hash,
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    log_operation(db, "import", old_path=str(target), new_path=str(target), document_id=doc.id)
    logger.info("上传文档: %s (status=pending)", target.name)
    return UploadResponse(document_id=doc.id, filename=target.name, status="pending")
