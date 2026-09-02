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
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/review", tags=["review"])
logger = get_logger("api.review")


def _category_template(db: Session, category_path: str) -> str:
    cat = db.query(Category).filter(Category.path == category_path).first()
    if cat and cat.rename_templates:
        for t in cat.rename_templates:
            if t.enabled:
                return t.template
    return DEFAULT_TEMPLATE


def _render_suggestion(db: Session, doc: Document, category_path: str, fields: dict) -> str:
    """按分类模板 + 字段渲染建议文件名。"""
    template = _category_template(db, category_path)
    render_fields = dict(fields)
    if doc.document_type and "document_type" not in render_fields:
        render_fields["document_type"] = doc.document_type
    suffix = Path(doc.current_path).suffix or Path(doc.original_filename).suffix
    return rename_service.render(template, render_fields, suffix)


def _fields_dict(doc: Document) -> dict[str, str]:
    return {f.field_name: f.field_value for f in doc.fields}


# 模板学习时优先纳入的"关键字段"
_TEMPLATE_CORE_FIELDS = [
    "date", "vessel", "contract_no", "material",
    "amount", "quantity", "invoice_no", "company", "order_no",
]


def _learn_template(db: Session, doc: Document, category_path: str) -> None:
    """审核确认归档成功后，记录识别模板（document_type -> 分类 + 关键字段）。

    下次再遇同类型且关键字段齐全的文件，classifier 命中模板直接自动归档。
    """
    doc_type = (doc.document_type or "").strip()
    if not doc_type or doc_type in ("其他", "未识别"):
        return
    fields = _fields_dict(doc)
    require = [f for f in _TEMPLATE_CORE_FIELDS if fields.get(f)]
    if not require:
        return
    tpl = (
        db.query(RecognitionTemplate)
        .filter(RecognitionTemplate.document_type == doc_type)
        .first()
    )
    if tpl is not None:
        tpl.category_path = category_path
        merged = sorted(set(tpl.require_fields_list) | set(require))
        tpl.require_fields = json.dumps(merged)
        tpl.enabled = True
        if not tpl.note:
            tpl.note = f"由 {doc.original_filename} 确认归档时学习"
    else:
        db.add(
            RecognitionTemplate(
                document_type=doc_type,
                category_path=category_path,
                require_fields=json.dumps(require),
                created_from_doc_id=doc.id,
                note=f"由 {doc.original_filename} 确认归档时学习",
            )
        )
    db.commit()
    logger.info("已学习识别模板: %s -> %s (require=%s)", doc_type, category_path, require)


def _upsert_fields(db: Session, doc: Document, fields: dict[str, str]) -> None:
    """把用户提交的字段写入文档档案（存在则更新，不存在则新增）。

    - suggested_category 是唯一字段，必须按字段名查找，不能按值查找；
    - 用户选定的分类也要落库，否则确认归档后分类丢失。
    """
    existing = {f.field_name: f for f in doc.fields}

    def _set(name: str, value: str) -> None:
        if name in existing:
            existing[name].field_value = value
        else:
            new = DocumentField(
                document_id=doc.id, field_name=name, field_value=value,
                confidence=1.0, source=SOURCE_USER,
            )
            db.add(new)
            existing[name] = new

    for name, value in fields.items():
        if name == "suggested_category":
            continue
        _set(name, str(value))

    if "suggested_category" in fields:
        _set("suggested_category", str(fields["suggested_category"]))


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

