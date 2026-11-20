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

from backend.config import settings
from backend.models import (
    SOURCE_USER,
    Category,
    Document,
    DocumentField,
    DocumentSample,
    RecognitionTemplate,
    ReviewDecision,
)
from backend.services.rename_service import DEFAULT_TEMPLATE, rename_service
from backend.services.rule_candidate_service import maybe_create_candidate
from backend.services.sample_service import build_fingerprint
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


# ---------------------------------------------------------------- 留痕与样本沉淀

_UNLEARNED_TYPES = {"", "其他", "未识别"}
# 太短的正文（如只有一行标题）不足以形成稳定版式指纹，沉淀样本无意义
_MIN_SAMPLE_TEXT = 60


def record_decision(
    db: Session,
    doc: Document,
    *,
    source: str = "single",
    final_category: str | None = None,
    final_document_type: str | None = None,
    suggested_category: str | None = None,
    suggested_document_type: str | None = None,
    action: str = "approve",
    sample_id: int | None = None,
    note: str = "",
) -> ReviewDecision:
    """写入一条审核决策留痕，返回记录。

    suggested_* 必须由调用方在覆盖前取好传进来：写在确认流程里再回读已经拿到的是新值，
    这也是之前"AI 建议"无处可查的根因。

    留痕失败不能影响归档主流程，异常一律吞掉并记日志。
    """
    decision = ReviewDecision(
        document_id=doc.id,
        action=action,
        source=source,
        suggested_category=suggested_category,
        suggested_document_type=suggested_document_type,
        confidence=doc.confidence,
        final_category=final_category,
        final_document_type=final_document_type,
        category_changed=bool(
            action == "approve"
            and suggested_category
            and final_category
            and suggested_category != final_category
        ),
        type_changed=bool(
            action == "approve"
            and suggested_document_type
            and final_document_type
            and suggested_document_type != final_document_type
        ),
        sample_id=sample_id,
        note=note,
    )
    try:
        db.add(decision)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("审核决策留痕失败（不影响归档） doc_id=%s", doc.id)
    return decision


def _learn_sample(db: Session, doc: Document, category_path: str) -> DocumentSample | None:
    """把确认归档的文档沉淀为格式样本（同类型+同分类已有样本则跳过）。

    复用已落库的 extracted_text 计算指纹，不重新 OCR，零 token、零额外解析。
    样本命中后由 classifier 直接自动归档，这是"人教机器"最直接的一环。
    """
    if not settings.LEARN_SAMPLE_ON_APPROVE:
        return None

    doc_type = (doc.document_type or "").strip()
    if doc_type in _UNLEARNED_TYPES:
        return None

    text = (doc.extracted_text or "").strip()
    if len(text) < _MIN_SAMPLE_TEXT:
        return None

    # 同类型同分类只留一个样本（含手工上传的），避免审核几十份同类单据后样本表爆炸
    exists = (
        db.query(DocumentSample)
        .filter(
            DocumentSample.document_type == doc_type,
            DocumentSample.category_path == category_path,
        )
        .first()
    )
    if exists is not None:
        return None

    fp = build_fingerprint(text)
    if not fp.get("labels") and not fp.get("grams"):
        return None

    sample = DocumentSample(
        document_type=doc_type,
        category_path=category_path,
        original_filename=doc.original_filename or doc.current_filename or "",
        fingerprint=json.dumps(fp, ensure_ascii=False),
        sample_doc_id=doc.id,
        note=f"由审核确认自动学习（文档 #{doc.id}）",
    )
    try:
        db.add(sample)
        db.commit()
        logger.info(
            "已沉淀格式样本 #%s: %s -> %s (labels=%d grams=%d)",
            sample.id, doc_type, category_path,
            len(fp.get("labels") or {}), len(fp.get("grams") or {}),
        )
        return sample
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("格式样本沉淀失败（不影响归档） doc_id=%s", doc.id)
        return None


def after_confirm(
    db: Session,
    doc: Document,
    *,
    category_path: str,
    suggested_category: str | None,
    suggested_document_type: str | None,
    source: str,
    note: str = "",
) -> dict:
    """确认归档成功后的统一后处理。

    四条确认路径（单条 / 批量 / 指纹簇）都走这里，保证学模板、沉淀样本、
    留痕、沉淀候选规则的口径完全一致，不因入口不同而漏记。

    返回本次沉淀了什么，供 API 回给前端做提示（"已沉淀样本"这类反馈闭环
    必须让人看得见，否则用户不知道自己在教机器）。
    """
    _learn_template(db, doc, category_path)
    sample = _learn_sample(db, doc, category_path)
    decision = record_decision(
        db,
        doc,
        source=source,
        final_category=category_path,
        final_document_type=doc.document_type,
        suggested_category=suggested_category,
        suggested_document_type=suggested_document_type,
        sample_id=sample.id if sample else None,
        note=note,
    )
    candidate = None
    # 只有"人工改判"才值得造规则：AI 猜对了不需要规则，AI 猜错说明有可固化的信号
    if decision.category_changed:
        candidate = maybe_create_candidate(
            db, doc, category_path, decision_id=decision.id
        )
        if candidate is not None:
            decision.rule_candidate_id = candidate.id
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("回填候选规则 id 失败（不影响归档） doc_id=%s", doc.id)
    return {"sample": sample, "decision": decision, "candidate": candidate}


def learning_feedback(payload: dict) -> dict:
    """把 after_confirm 的返回值压成给前端的提示结构。"""
    sample = payload.get("sample")
    candidate = payload.get("candidate")
    return {
        "learned_sample": bool(sample),
        "sample_id": getattr(sample, "id", None),
        "candidate_rule": (
            {
                "id": candidate.id,
                "keyword": candidate.keyword,
                "category_path": candidate.category_path,
                "hit_total": candidate.hit_total,
                "hit_target": candidate.hit_target,
            }
            if candidate is not None
            else None
        ),
    }
