"""候选规则 API：列表 / 采纳 / 拒绝。

采纳会生成一条真实的 FilenameRule（命中即跳过解析直接归档），
所以候选默认 pending，只能由用户显式采纳，不做自动提升。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ALL_CANDIDATE_STATUSES, CANDIDATE_PENDING, RuleCandidate
from backend.schemas.rule_candidate import RuleCandidateOut
from backend.schemas.filename_rule import FilenameRuleOut
from backend.services.rule_candidate_service import adopt_candidate, reject_candidate
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/rule-candidates", tags=["rule-candidates"])
logger = get_logger("api.rule_candidates")


@router.get("", response_model=list[RuleCandidateOut])
def list_rule_candidates(status: str = CANDIDATE_PENDING, db: Session = Depends(get_db)):
    """按状态列出候选规则，默认只看待采纳。"""
    q = db.query(RuleCandidate)
    if status and status != "all":
        if status not in ALL_CANDIDATE_STATUSES:
            raise HTTPException(status_code=400, detail=f"未知状态: {status}")
        q = q.filter(RuleCandidate.status == status)
    return q.order_by(RuleCandidate.id.desc()).all()


@router.post("/{candidate_id}/adopt", response_model=FilenameRuleOut)
def adopt(candidate_id: int, db: Session = Depends(get_db)):
    candidate = db.get(RuleCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选规则不存在")
    try:
        rule = adopt_candidate(db, candidate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    out = FilenameRuleOut.model_validate(rule)
    if rule.category is not None:
        out.category_name = rule.category.name
        out.category_path = rule.category.path
    return out


@router.post("/{candidate_id}/reject")
def reject(candidate_id: int, db: Session = Depends(get_db)):
    candidate = db.get(RuleCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选规则不存在")
    try:
        reject_candidate(db, candidate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}
