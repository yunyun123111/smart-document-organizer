"""人工审核 API：待确认列表 / 详情 / 修改 / 确认归档 / 跳过。"""
from __future__ import annotations

from pathlib import Path

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from backend.config import settings
from backend.database import get_db
from backend.models import (
    SOURCE_USER,
    STATUS_NEED_REVIEW,
    STATUS_SKIPPED,
    Category,
    Document,
    DocumentField,
    DocumentSample,
    RecognitionTemplate,
)
from backend.schemas.document import (
    DocumentDetail,
    DocumentListItem,
    DocumentUpdate,
    ReviewApproveRequest,
    ReviewGroup,
)
from backend.services.archive_service import ArchiveService
from backend.services.cluster_service import cluster_documents
from backend.services.rename_service import DEFAULT_TEMPLATE, rename_service
from backend.services.review_cluster_service import ReviewClusterService
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/review", tags=["review"])
logger = get_logger("api.review")


from backend.services.review_support import (
    _category_template,
    _fields_dict,
    _learn_template,
    _render_suggestion,
    _upsert_fields,
)


@router.get("", response_model=list[DocumentListItem])
def list_review(db: Session = Depends(get_db)):
    docs = (
        db.query(Document)
        .filter(Document.status == STATUS_NEED_REVIEW)
        .order_by(Document.created_at.desc())
        .all()
    )
    return docs


@router.get("/groups", response_model=list[ReviewGroup])
def list_review_groups(db: Session = Depends(get_db)):
    """智能批处理：待确认文件按 合同号→公司名→模板版式 聚类分组。"""
    docs = (
        db.query(Document)
        .options(joinedload(Document.fields))
        .filter(Document.status == STATUS_NEED_REVIEW)
        .order_by(Document.created_at.desc())
        .all()
    )
    return cluster_documents(docs)



class BatchConfirmClusterRequest(BaseModel):
    """指纹簇批量确认归档请求。"""
    base_document_id: int
    target_document_ids: list[int]
    category_path: str | None = None  # 可选：覆盖分类；缺省继承基准文档分类
    save_as_sample: bool = True


@router.get("/clusters", summary="获取按指纹聚类的待审核单据")
def get_review_clusters(db: Session = Depends(get_db)):
    """返回按文档指纹聚类后的待审核列表，便于前端折叠与批量确认。"""
    return ReviewClusterService.get_clustered_pending_reviews(db)


@router.post("/batch-confirm-cluster", summary="按指纹批次一键确认归档")
def batch_confirm_cluster(
    req: BatchConfirmClusterRequest,
    db: Session = Depends(get_db),
):
    """确认基准单据后，批量对整组同指纹单据执行自动归档。"""
    from backend.models import Document as _Document

    if db.get(_Document, req.base_document_id) is None:
        raise HTTPException(status_code=404, detail="基准文档不存在")
    result = ReviewClusterService.batch_confirm_cluster(
        db=db,
        base_doc_id=req.base_document_id,
        target_doc_ids=req.target_document_ids,
        category_path=req.category_path,
        save_as_sample=req.save_as_sample,
    )
    return {"code": 200, "message": "批量处理完成", "data": result}



# ---------- 识别模板管理 ----------

class TemplateUpdate(BaseModel):
    enabled: bool | None = None
    note: str | None = None


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    """列出全部识别模板（含 require_fields / usage_count）。"""
    tpls = (
        db.query(RecognitionTemplate)
        .order_by(RecognitionTemplate.document_type)
        .all()
    )
    return [
        {
            "id": t.id,
            "document_type": t.document_type,
            "category_path": t.category_path,
            "require_fields": t.require_fields_list,
            "usage_count": t.usage_count,
            "enabled": t.enabled,
            "note": t.note,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tpls
    ]


@router.put("/templates/{tpl_id}")
def update_template(tpl_id: int, req: TemplateUpdate, db: Session = Depends(get_db)):
    tpl = db.get(RecognitionTemplate, tpl_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    if req.enabled is not None:
        tpl.enabled = req.enabled
    if req.note is not None:
        tpl.note = req.note
    db.commit()
    return {"ok": True, "enabled": tpl.enabled}


@router.delete("/templates/{tpl_id}")
def delete_template(tpl_id: int, db: Session = Depends(get_db)):
    tpl = db.get(RecognitionTemplate, tpl_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(tpl)
    db.commit()
    return {"ok": True}
@router.get("/{doc_id}")
def get_review_detail(doc_id: int, db: Session = Depends(get_db)):
    """待审核详情：文档 + 字段 + 建议分类/文件名 + 可选分类。"""
    doc = (
        db.query(Document)
        .options(joinedload(Document.fields))
        .filter(Document.id == doc_id, Document.status == STATUS_NEED_REVIEW)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不在待审核列表")

    fields = _fields_dict(doc)
    category_path = fields.get("suggested_category") or "其他"
    return {
        "document": doc,
        "fields": fields,
        "suggested_category": category_path,
        "suggested_filename": _render_suggestion(db, doc, category_path, fields),
        "categories": [c.path for c in db.query(Category).filter(Category.enabled.is_(True)).all()],
        "templates": {
            "default": DEFAULT_TEMPLATE,
            "category": _category_template(db, category_path),
        },
    }


@router.put("/{doc_id}")
def update_review(doc_id: int, req: DocumentUpdate, db: Session = Depends(get_db)):
    """修改待审核文档的类型 / 字段。"""
    doc = (
        db.query(Document)
        .options(joinedload(Document.fields))
        .filter(Document.id == doc_id, Document.status == STATUS_NEED_REVIEW)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不在待审核列表")

    if req.document_type is not None:
        doc.document_type = req.document_type
    if req.title is not None:
        doc.title = req.title

    if req.fields:
        _upsert_fields(db, doc, req.fields)
    db.commit()
    return {"ok": True}


@router.post("/{doc_id}/approve")
def approve_review(doc_id: int, req: ReviewApproveRequest, db: Session = Depends(get_db)):
    """确认归档：按当前类型/分类/字段归档。"""
    doc = (
        db.query(Document)
        .options(joinedload(Document.fields))
        .filter(Document.id == doc_id, Document.status == STATUS_NEED_REVIEW)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不在待审核列表")

    if req.document_type is not None:
        doc.document_type = req.document_type

    fields = _fields_dict(doc)  # 本次请求前的字段值
    category_path = (
        req.category_path
        or (req.fields or {}).get("suggested_category")
        or fields.get("suggested_category")
        or "其他"
    )
    fields.update(req.fields or {})
    fields["suggested_category"] = category_path  # 用户选定分类要落库

    if req.fields:
        _upsert_fields(db, doc, req.fields)
    else:
        _upsert_fields(db, doc, {"suggested_category": category_path})
    db.commit()  # 提交字段与分类，确保后续归档失败时人工修改不丢失

    if req.filename:
        filename = req.filename
    else:
        filename = _render_suggestion(db, doc, category_path, fields)

    source = Path(doc.current_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="原文件已不存在，无法归档")

    archive = ArchiveService(db)
    ar = archive.archive(
        source,
        category_path=category_path,
        filename=filename,
        date_str=fields.get("date"),
        document_id=doc.id,
    )
    if not ar.success:
        raise HTTPException(status_code=400, detail=ar.error or "归档失败")
    # 学习同类文件模板（下次自动归档）
    _learn_template(db, doc, category_path)
    return {"ok": True, "path": str(ar.document_path)}


@router.post("/{doc_id}/skip")
def skip_review(doc_id: int, db: Session = Depends(get_db)):
    """跳过该文档（标记为已跳过）。"""
    doc = (
        db.query(Document)
        .filter(Document.id == doc_id, Document.status == STATUS_NEED_REVIEW)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不在待审核列表")
    doc.status = STATUS_SKIPPED
    db.commit()
    return {"ok": True}


class BatchApproveRequest(BaseModel):
    """批量确认归档请求。"""
    doc_ids: list[int]
    category_path: str | None = None  # 可选：统一覆盖分类


@router.post("/batch-approve")
def batch_approve(req: BatchApproveRequest, db: Session = Depends(get_db)):
    """批量确认归档：按各自建议分类/文件名直接归档，逐个返回结果。"""
    results: list[dict] = []
    for doc_id in req.doc_ids:
        doc = (
            db.query(Document)
            .options(joinedload(Document.fields))
            .filter(Document.id == doc_id, Document.status == STATUS_NEED_REVIEW)
            .first()
        )
        if not doc:
            results.append({"id": doc_id, "success": False, "error": "不存在或不在待审核列表"})
            continue
        fields = _fields_dict(doc)
        category_path = (
            req.category_path
            or fields.get("suggested_category")
            or "其他"
        )
        filename = _render_suggestion(db, doc, category_path, fields)
        source = Path(doc.current_path)
        if not source.exists():
            results.append({"id": doc_id, "success": False, "error": "原文件已不存在，无法归档"})
            continue
        archive = ArchiveService(db)
        ar = archive.archive(
            source,
            category_path=category_path,
            filename=filename,
            date_str=fields.get("date"),
            document_id=doc.id,
        )
        if ar.success:
            _learn_template(db, doc, category_path)
            results.append({"id": doc_id, "success": True, "path": str(ar.document_path)})
        else:
            results.append({"id": doc_id, "success": False, "error": ar.error or "归档失败"})
    ok = sum(1 for r in results if r["success"])
    logger.info("批量归档完成: %d/%d 成功", ok, len(results))
    return {"ok": True, "success_count": ok, "failed_count": len(results) - ok, "results": results}

