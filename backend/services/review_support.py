"""review_support.py：人工审核流程的公共支持函数。

从 backend/api/review.py 抽取（修复 review_cluster_service 与 review API 的循环导入）：
- _category_template / _render_suggestion：按分类模板渲染建议文件名
- _fields_dict / _upsert_fields：文档字段读写（suggested_category 落库）
- _learn_template / _TEMPLATE_CORE_FIELDS：确认归档后学习识别模板 + 增强格式样本

review.py 与 review_cluster_service.py 均从本模块导入，保证批量继承归档
与单文件审核行为完全一致，避免逻辑漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models import (
    SOURCE_USER,
    Category,
    Document,
    DocumentField,
    DocumentSample,
    RecognitionTemplate,
)
from backend.services.rename_service import DEFAULT_TEMPLATE, rename_service
from backend.utils.logger import get_logger

logger = get_logger("api.review")

# 模板学习时优先纳入的"关键字段"
_TEMPLATE_CORE_FIELDS = [
    "date", "vessel", "contract_no", "material",
    "amount", "quantity", "invoice_no", "company", "order_no",
]


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

    # 人工反馈增强：把本次确认文档的字段并入同类格式样本锚点（越用越准）
    try:
        samples = (
            db.query(DocumentSample)
            .filter(
                DocumentSample.document_type == doc_type,
                DocumentSample.category_path == category_path,
                DocumentSample.enabled.is_(True),
            )
            .all()
        )
        if samples:
            new_fields = sorted({f.field_name for f in doc.fields})
            for s in samples:
                fp = s.fingerprint_dict
                old = set(fp.get("fields") or [])
                merged = sorted(old | set(new_fields))
                if merged != sorted(old):
                    fp["fields"] = merged
                    s.fingerprint = json.dumps(fp, ensure_ascii=False)
            db.commit()
            logger.info("格式样本锚点已增强: %s -> %s (%d 个样本)", doc_type, category_path, len(samples))
    except Exception:  # noqa: BLE001
        logger.exception("格式样本锚点增强失败（不影响归档）")


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
