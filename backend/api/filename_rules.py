"""文件名规则 API：按文件名直接归档的规则 CRUD。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Category, FilenameRule
from backend.schemas.filename_rule import (
    FilenameRuleCreate,
    FilenameRuleOut,
    FilenameRuleUpdate,
)
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/filename-rules", tags=["filename-rules"])
logger = get_logger("api.filename_rules")


def _to_out(rule: FilenameRule) -> FilenameRuleOut:
    out = FilenameRuleOut.model_validate(rule)
    cat = rule.category
    if cat is not None:
        out.category_name = cat.name
        out.category_path = cat.path
    return out


@router.get("", response_model=list[FilenameRuleOut])
def list_filename_rules(category_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(FilenameRule).order_by(FilenameRule.priority.desc(), FilenameRule.id)
    if category_id is not None:
        q = q.filter(FilenameRule.category_id == category_id)
    return [_to_out(r) for r in q.all()]


@router.post("", response_model=FilenameRuleOut)
def create_filename_rule(req: FilenameRuleCreate, db: Session = Depends(get_db)):
    if not db.get(Category, req.category_id):
        raise HTTPException(status_code=400, detail="分类不存在")
    try:
        re.compile(req.pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"正则非法: {e}") from e
    rule = FilenameRule(**req.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _to_out(rule)


@router.put("/{rule_id}", response_model=FilenameRuleOut)
def update_filename_rule(rule_id: int, req: FilenameRuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(FilenameRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="文件名规则不存在")
    data = req.model_dump(exclude_unset=True)
    if "category_id" in data and not db.get(Category, data["category_id"]):
        raise HTTPException(status_code=400, detail="分类不存在")
    if "pattern" in data:
        try:
            re.compile(data["pattern"])
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"正则非法: {e}") from e
    for k, v in data.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return _to_out(rule)


@router.delete("/{rule_id}")
def delete_filename_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(FilenameRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="文件名规则不存在")
    db.delete(rule)
    db.commit()
    return {"ok": True}
