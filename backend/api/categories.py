"""分类管理 API：树形结构 / CRUD。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Category
from backend.schemas.category import (
    CategoryCreate,
    CategoryOut,
    CategoryTreeOut,
    CategoryUpdate,
)
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/categories", tags=["categories"])
logger = get_logger("api.categories")


def _build_tree(cats: list[Category], parent_id: int | None = None) -> list[CategoryTreeOut]:
    nodes = [c for c in cats if c.parent_id == parent_id]
    nodes.sort(key=lambda c: c.sort_order)
    result = []
    for c in nodes:
        node = CategoryTreeOut.model_validate(c)
        node.children = _build_tree(cats, c.id)
        result.append(node)
    return result


@router.get("", response_model=list[CategoryTreeOut])
def list_categories(db: Session = Depends(get_db)):
    cats = db.query(Category).order_by(Category.sort_order).all()
    return _build_tree(cats)


@router.get("/all", response_model=list[CategoryOut])
def list_categories_flat(db: Session = Depends(get_db)):
    return db.query(Category).order_by(Category.sort_order).all()


@router.post("", response_model=CategoryOut)
def create_category(req: CategoryCreate, db: Session = Depends(get_db)):
    parent_path = ""
    if req.parent_id:
        parent = db.get(Category, req.parent_id)
        if not parent:
            raise HTTPException(status_code=400, detail="父分类不存在")
        parent_path = parent.path
    path = f"{parent_path}/{req.name}" if parent_path else req.name
    if db.query(Category).filter(Category.path == path).first():
        raise HTTPException(status_code=400, detail="分类路径已存在")
    cat = Category(
        parent_id=req.parent_id,
        name=req.name,
        path=path,
        description=req.description,
        sort_order=req.sort_order,
        enabled=req.enabled,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    logger.info("创建分类: %s", path)
    return cat


@router.put("/{cat_id}", response_model=CategoryOut)
def update_category(cat_id: int, req: CategoryUpdate, db: Session = Depends(get_db)):
    cat = db.get(Category, cat_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(cat, k, v)
    db.commit()
    db.refresh(cat)
    return cat


@router.delete("/{cat_id}")
def delete_category(cat_id: int, db: Session = Depends(get_db)):
    cat = db.get(Category, cat_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    children = db.query(Category).filter(Category.parent_id == cat_id).count()
    if children:
        raise HTTPException(status_code=400, detail="请先删除子分类")
    db.delete(cat)
    db.commit()
    return {"ok": True}
