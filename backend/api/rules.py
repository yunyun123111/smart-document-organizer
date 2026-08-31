"""规则 / 重命名模板管理 API：CRUD。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import RenameTemplate, Rule
from backend.schemas.rule import (
    RenameTemplateCreate,
    RenameTemplateOut,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/rules", tags=["rules"])
logger = get_logger("api.rules")


# ---------- 规则 ----------
@router.get("", response_model=list[RuleOut])
def list_rules(category_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Rule).order_by(Rule.priority.desc(), Rule.id)
    if category_id is not None:
        q = q.filter(Rule.category_id == category_id)
    return q.all()


@router.post("", response_model=RuleOut)
def create_rule(req: RuleCreate, db: Session = Depends(get_db)):
    if not db.get(Category, req.category_id):
        raise HTTPException(status_code=400, detail="分类不存在")
    if req.match_type not in ("contains", "exact", "regex"):
        raise HTTPException(status_code=400, detail="match_type 非法")
    rule = Rule(**req.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, req: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    data = req.model_dump(exclude_unset=True)
    if "category_id" in data and not db.get(Category, data["category_id"]):
        raise HTTPException(status_code=400, detail="分类不存在")
    for k, v in data.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(rule)
    db.commit()
    return {"ok": True}


# ---------- 重命名模板 ----------
@router.get("/templates", response_model=list[RenameTemplateOut])
def list_templates(category_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(RenameTemplate).order_by(RenameTemplate.id)
    if category_id is not None:
        q = q.filter(RenameTemplate.category_id == category_id)
    return q.all()


@router.post("/templates", response_model=RenameTemplateOut)
def create_template(req: RenameTemplateCreate, db: Session = Depends(get_db)):
    if not db.get(Category, req.category_id):
        raise HTTPException(status_code=400, detail="分类不存在")
    tpl = RenameTemplate(**req.model_dump())
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.delete("/templates/{tpl_id}")
def delete_template(tpl_id: int, db: Session = Depends(get_db)):
    tpl = db.get(RenameTemplate, tpl_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(tpl)
    db.commit()
    return {"ok": True}


from backend.models import Category  # noqa: E402
