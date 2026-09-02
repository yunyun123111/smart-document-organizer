"""文档库 API：列表 / 详情 / 上传 / 删除 / 预览。"""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

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
    RESULT_OK,
    STATUS_NEED_REVIEW,
    STATUS_PROCESSING,
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
from backend.services.operation_service import log_operation
from backend.services.processing_service import processing_service
from backend.utils.file_utils import get_file_type, get_mime_type
from backend.utils.filename_utils import safe_filename, unique_filename
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
    query = db.query(Document).order_by(Document.created_at.desc())
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
    return docs


@router.get("/suggest")
def suggest_documents(keyword: str = "", db: Session = Depends(get_db)):
    """搜索建议：类型 / 公司 / 合同号 / 发票号 / 船名 / 物料 / 文件名。"""
    return {"suggestions": suggest(db, keyword)}


@router.get("/types")
def list_document_types(db: Session = Depends(get_db)):
    """返回全部文档类型（供筛选下拉）。"""
    rows = db.query(Document.document_type).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


@router.get("/categories")
def list_document_categories(db: Session = Depends(get_db)):
    """返回全部归档分类路径（供筛选下拉）。"""
    rows = (
        db.query(DocumentField.field_value)
        .filter(DocumentField.field_name == "suggested_category")
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


class BatchDeleteRequest(BaseModel):
    doc_ids: list[int]


@router.post("/batch-delete")
def batch_delete_documents(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    """批量删除文档（记录 + 磁盘文件）。"""
    deleted: list[int] = []
    missing: list[int] = []
    for doc_id in req.doc_ids:
        doc = db.get(Document, doc_id)
        if not doc:
            missing.append(doc_id)
            continue
        path = Path(doc.current_path or doc.original_path)
        existed = path.is_file()
        if existed:
            log_operation(
                db,
                OP_DELETE,
                old_path=str(path),
                new_path="",
                document_id=doc.id,
                result=RESULT_OK,
                error_message="批量删除，记录与磁盘文件一并删除，不可撤销",
            )
            path.unlink(missing_ok=True)
        db.delete(doc)
        deleted.append(doc_id)
    db.commit()
    logger.info("批量删除文档: %d 条（缺失 %d）", len(deleted), len(missing))
    return {"ok": True, "deleted_count": len(deleted), "missing_count": len(missing)}


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
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已被移动或删除")
    media_type = doc.mime_type or "application/octet-stream"
    return FileResponse(str(path), media_type=media_type, filename=doc.current_filename)


class RenameRequest(BaseModel):
    filename: str


@router.post("/{doc_id}/rename")
def rename_document(doc_id: int, req: RenameRequest, db: Session = Depends(get_db)):
    """手动重命名文档：移动磁盘文件 + 更新记录 + 审计日志。"""
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    src = Path(doc.current_path or doc.original_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail="磁盘文件不存在，无法重命名")
    ext = src.suffix
    base = (req.filename or "").strip().strip('"')
    if not base:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    new_name = safe_filename(base, ext)
    if new_name == src.name:
        return {"ok": True, "filename": src.name, "path": str(src), "changed": False}
    target = unique_filename(src.parent, new_name)
    try:
        shutil.move(str(src), str(target))
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
    db.commit()
    logger.info("手动重命名: %s -> %s (doc#%s)", src.name, target.name, doc.id)
    return {"ok": True, "filename": target.name, "path": str(target), "changed": True}


@router.delete("/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = Path(doc.current_path or doc.original_path)
    existed = path.is_file()
    if existed:
        # 删除属于不可逆操作：先记审计日志，保证事后可追溯（规格书第六节）
        log_operation(
            db,
            OP_DELETE,
            old_path=str(path),
            new_path="",
            document_id=doc.id,
            result=RESULT_OK,
            error_message="文档记录与磁盘文件一并删除，不可撤销",
        )
        path.unlink(missing_ok=True)
    db.delete(doc)
    db.commit()
    logger.info("删除文档: %s (磁盘文件%s)", path.name, "已删除" if existed else "不存在")
    return {"ok": True, "file_deleted": existed}


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传文件到 inbox 并登记文档（待整理）。"""
    inbox = settings.inbox_root
    Path(inbox).mkdir(parents=True, exist_ok=True)
    safe_name = file.filename.replace("\\", "_").replace("/", "_")
    target = Path(inbox) / safe_name
    content = await file.read()
    # 重名时递增
    if target.exists():
        base = target.stem
        i = 1
        while target.exists():
            target = Path(inbox) / f"{base}_{i}{target.suffix}"
            i += 1
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
