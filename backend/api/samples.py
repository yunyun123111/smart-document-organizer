"""文档格式样本管理 API（人工样例学习）。

POST 上传一份清晰样本（指定 文档类型 + 归档分类），
系统本地 OCR/解析提取版式指纹，后续同版式文档零 AI 自动归档。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import DocumentSample
from backend.services.ocr_service import ocr_service
from backend.services.parser_service import parser_service
from backend.services.sample_service import build_fingerprint
from backend.services.text_service import clean_parsed_document
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/samples", tags=["samples"])
logger = get_logger("api.samples")

# 样本原始文件目录（data/samples），与文档库分开
SAMPLE_DIR = Path(settings.DOCUMENT_ROOT).parent / "samples"


def _sample_out(s: DocumentSample) -> dict:
    fp = s.fingerprint_dict
    return {
        "id": s.id,
        "document_type": s.document_type,
        "category_path": s.category_path,
        "original_filename": s.original_filename,
        "usage_count": s.usage_count,
        "enabled": s.enabled,
        "note": s.note,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "summary": {
            "labels": len(fp.get("labels") or {}),
            "grams": len(fp.get("grams") or {}),
            "fields": fp.get("fields") or [],
            "stats": fp.get("stats") or {},
        },
    }


@router.get("")
def list_samples(db: Session = Depends(get_db)):
    rows = db.query(DocumentSample).order_by(DocumentSample.id.desc()).all()
    return [_sample_out(s) for s in rows]


class SamplePatch(BaseModel):
    document_type: str | None = None
    category_path: str | None = None
    enabled: bool | None = None
    note: str | None = None


@router.patch("/{sample_id}")
def update_sample(sample_id: int, patch: SamplePatch, db: Session = Depends(get_db)):
    s = db.query(DocumentSample).filter(DocumentSample.id == sample_id).first()
    if not s:
        raise HTTPException(404, "样本不存在")
    if patch.document_type is not None:
        s.document_type = patch.document_type.strip()
    if patch.category_path is not None:
        s.category_path = patch.category_path.strip()
    if patch.enabled is not None:
        s.enabled = patch.enabled
    if patch.note is not None:
        s.note = patch.note
    db.commit()
    db.refresh(s)
    return _sample_out(s)


@router.delete("/{sample_id}")
def delete_sample(sample_id: int, db: Session = Depends(get_db)):
    s = db.query(DocumentSample).filter(DocumentSample.id == sample_id).first()
    if not s:
        raise HTTPException(404, "样本不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("")
def create_sample(
    file: UploadFile = File(...),
    document_type: str = Form(...),
    category_path: str = Form(...),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """上传样本并学习指纹。"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "sample").suffix or ".pdf"
    target = SAMPLE_DIR / f"sample_{int(time.time())}_{Path(file.filename or 's').stem[:20]}{suffix}"
    try:
        target.write_bytes(file.file.read())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"样本保存失败: {e}") from e

    # 解析 -> 文本（扫描件补 OCR）
    try:
        parsed = parser_service.parse_file(target)
        parsed = clean_parsed_document(parsed)
        text = parsed.text or ""
        if parsed.needs_ocr:
            if parsed.metadata.get("file_type") == "pdf":
                pages = ocr_service.recognize_pdf(target)
                text = "\n".join(p.text for p in pages if p.text)
            else:
                text = ocr_service.recognize_image_file(target).text
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"样本解析失败: {e}") from e

    if not text.strip():
        raise HTTPException(422, "未能从样本中提取到文本，请换更清晰的文档")

    fp = build_fingerprint(text)
    if not fp.get("labels") and not fp.get("grams"):
        raise HTTPException(422, "样本特征过少，无法学习，请换更清晰的文档")

    s = DocumentSample(
        document_type=document_type.strip(),
        category_path=category_path.strip(),
        original_filename=file.filename or target.name,
        fingerprint=json.dumps(fp, ensure_ascii=False),
        note=note,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    logger.info("已学习格式样本 #%s %s -> %s", s.id, s.original_filename, s.category_path)
    return _sample_out(s)
