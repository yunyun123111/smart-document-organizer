"""rule_candidate_service.py：人工改判 -> 文件名前缀候选规则。

只做"可验证"的事：
- 前缀从真实文件名提取，不做语义猜测；
- 命中数是当前库扫描结果，不是预测；
- 候选默认 pending，绝不自动改识别行为（FilenameRule 命中会跳过 OCR/AI 直接归档）。
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from backend.models import (
    CANDIDATE_ADOPTED,
    CANDIDATE_PENDING,
    CANDIDATE_REJECTED,
    KIND_FILENAME_PREFIX,
    Category,
    Document,
    FilenameRule,
    RuleCandidate,
)
from backend.utils.logger import get_logger

logger = get_logger("services.rule_candidate")

# 业务编码前缀：连续 4~8 位大写字母（SJWLXS / HSYCXS / HSYCCG）
_PREFIX_RE = re.compile(r"[A-Z]{4,8}")
# 紧随 8 位以上数字的是业务编号（SOTC202610210002 / BOTC202610220001），不是分类前缀
_NUMBER_TAIL_RE = re.compile(r"[（(]?\s*\d{8,}")
# 太少见的前缀不值得造规则（库里只命中 1 份，说明是孤例）
MIN_HIT_TOTAL = 2


def extract_prefix(filename: str) -> str | None:
    """从文件名提取候选前缀；取第一个不像业务编号的大写字母段。"""
    stem = Path(filename or "").stem
    if not stem:
        return None
    for m in _PREFIX_RE.finditer(stem):
        tail = stem[m.end():]
        if _NUMBER_TAIL_RE.match(tail.lstrip()):
            continue
        return m.group(0)
    return None


def _category_of(doc: Document) -> str:
    for f in doc.fields:
        if f.field_name == "suggested_category":
            return f.field_value or ""
    return ""


def scan_hits(db: Session, prefix: str, category_path: str) -> tuple[int, int]:
    """扫描当前库：文件名命中该前缀的文档数 / 其中已在目标分类的数量。"""
    pattern = re.compile(prefix, re.IGNORECASE)
    docs = (
        db.query(Document)
        .options(joinedload(Document.fields))
        .filter(Document.status != "recycled")
        .all()
    )
    total = 0
    target = 0
    for d in docs:
        names = (d.current_filename or "", d.original_filename or "")
        if not any(pattern.search(n) for n in names if n):
            continue
        total += 1
        if _category_of(d) == category_path:
            target += 1
    return total, target


def maybe_create_candidate(
    db: Session,
    doc: Document,
    category_path: str,
    *,
    decision_id: int | None = None,
) -> RuleCandidate | None:
    """人工改判后尝试沉淀一条候选规则。不满足条件返回 None（不报错）。"""
    try:
        # 用 original_filename：归档后 current_filename 已按模板重命名，不再带业务编码前缀
        prefix = extract_prefix(doc.original_filename or doc.current_filename or "")
        if not prefix:
            return None

        # 同前缀同分类已有候选（待采纳/已采纳）→ 不重复
        duplicated = (
            db.query(RuleCandidate)
            .filter(
                RuleCandidate.keyword == prefix,
                RuleCandidate.category_path == category_path,
                RuleCandidate.status.in_([CANDIDATE_PENDING, CANDIDATE_ADOPTED]),
            )
            .first()
        )
        if duplicated is not None:
            return None

        # 该前缀已被任何文件名规则占用（不论指向哪个分类）→ 不生成候选。
        # 理由：FilenameRule 命中即跳过识别直接归档，两条规则争同一前缀会互相抢命中，
        # 且这种前缀通常已在业务上绑定固定分类，为个例再造一条规则风险大于收益。
        for rule in db.query(FilenameRule).all():
            pat = (rule.pattern or "").strip().lower()
            if not pat:
                continue
            if pat == prefix.lower() or pat.startswith(prefix.lower()):
                return None

        total, target = scan_hits(db, prefix, category_path)
        if total < MIN_HIT_TOTAL:
            return None

        candidate = RuleCandidate(
            kind=KIND_FILENAME_PREFIX,
            keyword=prefix,
            category_path=category_path,
            document_type=(doc.document_type or "") or None,
            from_doc_id=doc.id,
            from_decision_id=decision_id,
            status=CANDIDATE_PENDING,
            hit_total=total,
            hit_target=target,
            note=f"人工改判沉淀：{doc.original_filename or doc.current_filename}",
        )
        db.add(candidate)
        db.commit()
        logger.info(
            "生成候选规则 #%s: %s -> %s（命中 %d 份，其中 %d 份已在目标分类）",
            candidate.id, prefix, category_path, total, target,
        )
        return candidate
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("候选规则生成失败（不影响归档） doc_id=%s", doc.id)
        return None


def adopt_candidate(db: Session, candidate: RuleCandidate) -> FilenameRule:
    """采纳候选：创建一条启用中的 FilenameRule。分类不存在时抛 ValueError。"""
    if candidate.status != CANDIDATE_PENDING:
        raise ValueError("该候选已被处理")

    cat = db.query(Category).filter(Category.path == candidate.category_path).first()
    if cat is None:
        raise ValueError(f"目标分类不存在: {candidate.category_path}")
    if not cat.enabled:
        raise ValueError(f"目标分类已停用: {candidate.category_path}")

    existing = (
        db.query(FilenameRule)
        .filter(FilenameRule.category_id == cat.id, FilenameRule.pattern == candidate.keyword)
        .first()
    )
    if existing is not None:
        raise ValueError(f"已存在相同文件名规则: {candidate.keyword}")

    rule = FilenameRule(
        category_id=cat.id,
        pattern=candidate.keyword,
        priority=0,
        note=f"由候选规则 #{candidate.id} 采纳",
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    candidate.status = CANDIDATE_ADOPTED
    candidate.adopted_rule_id = rule.id
    db.commit()
    logger.info("候选规则 #%s 已采纳 -> FilenameRule #%s(%s)", candidate.id, rule.id, candidate.keyword)
    return rule


def reject_candidate(db: Session, candidate: RuleCandidate) -> None:
    if candidate.status != CANDIDATE_PENDING:
        raise ValueError("该候选已被处理")
    candidate.status = CANDIDATE_REJECTED
    db.commit()
