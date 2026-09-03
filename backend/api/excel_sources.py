"""Excel 登记表数据源 API。

上传 Excel 台账工作簿 -> 自动解析每个 sheet、识别列映射 ->
配置列映射 / 主键列 / 启用状态 -> 索引用于识别流程中的权威数据源匹配。

鉴权：走现有 /api 前缀 + Bearer 访问密码中间件。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import ExcelSheetConfig, ExcelSource
from backend.services.excel_matcher import (
    detect_column_map,
    detect_key_column,
    excel_matcher,
)
from backend.utils.filename_utils import safe_filename
from backend.utils.fs_path import fs_mkdir
from backend.utils.logger import get_logger

logger = get_logger("api.excel_sources")

router = APIRouter(prefix="/api/excel-sources", tags=["excel-sources"])


class SheetConfigRequest(BaseModel):
    enabled: bool | None = None
    doc_type_hint: str | None = None
    column_map: dict[str, str] | None = None
    key_column: str | None = None


class SourceUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class TestMatchRequest(BaseModel):
    fields: dict[str, str]


def _scan_workbook(path: Path) -> dict[str, dict]:
    """解析工作簿：返回 {sheet_name: {column_map, key_column, headers}}。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    result: dict[str, dict] = {}
    try:
        for ws in wb.worksheets:
            headers: list[str] = []
            for row in ws.iter_rows(values_only=True):
                if any(c is not None and str(c).strip() != "" for c in row):
                    headers = [str(c) if c is not None else "" for c in row]
                    break
            result[ws.title] = {
                "column_map": detect_column_map(headers),
                "key_column": detect_key_column(headers),
                "headers": headers,
            }
    finally:
        wb.close()
    return result


@router.post("/upload")
def upload_source(file: UploadFile, db: Session = Depends(get_db)):
    """上传 Excel 工作簿，自动解析每个 sheet 并识别列映射。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")
    if not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xls / .xlsm 文件")

    raw = file.file.read()
    file_hash = hashlib.sha256(raw).hexdigest()
    # 重复上传：同一内容直接返回已有数据源
    exist = db.query(ExcelSource).filter(ExcelSource.file_hash == file_hash).first()
    if exist:
        return {"ok": True, "duplicate": True, "source": exist.to_dict()}

    fs_mkdir(settings.excel_sources_root)
    safe_name = safe_filename(file.filename) or "登记表.xlsx"
    if not safe_name.lower().endswith((".xlsx", ".xls", ".xlsm")):
        safe_name += ".xlsx"
    target = settings.excel_sources_root / safe_name
    # 避免重名覆盖
    n = 1
    stem, suffix = target.stem, target.suffix
    while target.exists():
        target = settings.excel_sources_root / f"{stem}_{n}{suffix}"
        n += 1
    target.write_bytes(raw)

    try:
        sheets_info = _scan_workbook(target)
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"无法解析 Excel 文件: {e}") from e

    # 同名更新：重新上传同名工作簿视为"更新该数据源"（内容新增/修正后重传）。
    # 保留 source id 与 name，替换文件副本 + 重新扫描列映射 + 重建 sheet 配置，
    # 避免产生新旧两个数据源导致匹配混乱（旧数据源优先命中）。
    same = (
        db.query(ExcelSource)
        .filter(ExcelSource.name == file.filename)
        .order_by(ExcelSource.id.desc())
        .first()
    )
    if same is not None:
        old_file = Path(same.file_path)
        old_cfgs = {oc.sheet_name: oc for oc in same.sheets}
        for oc in same.sheets:
            db.delete(oc)
        db.flush()
        same.file_hash = file_hash
        same.total_sheets = len(sheets_info)
        same.file_path = str(target)
        if old_file.exists() and str(old_file.resolve()) != str(target.resolve()):
            old_file.unlink(missing_ok=True)
        db.flush()
        for sheet_name, info in sheets_info.items():
            old_cfg = old_cfgs.get(sheet_name)
            db.add(ExcelSheetConfig(
                source_id=same.id,
                sheet_name=sheet_name,
                enabled=old_cfg.enabled if old_cfg else bool(info["column_map"]),
                doc_type_hint=old_cfg.doc_type_hint if old_cfg else "",
                column_map=json.dumps(info["column_map"], ensure_ascii=False),
                key_column=info["key_column"],
                row_count=0,
            ))
        db.commit()
        excel_matcher.reload_source(db, same.id)
        logger.info("Excel 数据源同名更新：替换 #%s（%s，%d 个 sheet）",
                    same.id, file.filename, len(sheets_info))
        return {"ok": True, "duplicate": False, "replaced": True,
                "source": db.get(ExcelSource, same.id).to_dict()}

    src = ExcelSource(
        name=file.filename, file_path=str(target), file_hash=file_hash,
        enabled=True, total_sheets=len(sheets_info),
    )
    db.add(src)
    db.flush()

    for sheet_name, info in sheets_info.items():
        cfg = ExcelSheetConfig(
            source_id=src.id,
            sheet_name=sheet_name,
            enabled=bool(info["column_map"]),  # 能识别到列映射的 sheet 默认启用
            doc_type_hint="",
            column_map=json.dumps(info["column_map"], ensure_ascii=False),
            key_column=info["key_column"],
            row_count=0,
        )
        db.add(cfg)
    db.commit()
    excel_matcher.reload_source(db, src.id)
    return {"ok": True, "duplicate": False, "source": db.get(ExcelSource, src.id).to_dict()}


@router.get("")
def list_sources(db: Session = Depends(get_db)):
    """所有 Excel 数据源（含各 sheet 配置）。"""
    sources = db.query(ExcelSource).order_by(ExcelSource.id.desc()).all()
    return {"ok": True, "items": [s.to_dict() for s in sources]}


@router.get("/{source_id}")
def get_source(source_id: int, db: Session = Depends(get_db)):
    src = db.get(ExcelSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return {"ok": True, "source": src.to_dict()}


@router.put("/{source_id}")
def update_source(source_id: int, req: SourceUpdateRequest, db: Session = Depends(get_db)):
    src = db.get(ExcelSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if req.name is not None:
        src.name = req.name
    if req.enabled is not None:
        src.enabled = req.enabled
    db.commit()
    excel_matcher.reload_source(db, src.id)
    return {"ok": True, "source": src.to_dict()}


@router.put("/{source_id}/sheets/{sheet_id}")
def update_sheet(source_id: int, sheet_id: int, req: SheetConfigRequest, db: Session = Depends(get_db)):
    cfg = db.get(ExcelSheetConfig, sheet_id)
    if cfg is None or cfg.source_id != source_id:
        raise HTTPException(status_code=404, detail="sheet 配置不存在")
    if req.enabled is not None:
        cfg.enabled = req.enabled
    if req.doc_type_hint is not None:
        cfg.doc_type_hint = req.doc_type_hint
    if req.key_column is not None:
        cfg.key_column = req.key_column
    if req.column_map is not None:
        # 校验列映射字段
        for k in req.column_map:
            if k not in ("company", "seller", "date", "contract_no", "contract_suffix",
                         "order_no", "invoice_no", "amount", "vessel", "material",
                         "quantity", "unit_price"):
                raise HTTPException(status_code=400, detail=f"未知字段名: {k}")
        cfg.column_map = json.dumps(req.column_map, ensure_ascii=False)
    db.commit()
    excel_matcher.reload_source(db, source_id)
    return {"ok": True, "sheet": cfg.to_dict()}


@router.post("/{source_id}/reload")
def reload_source(source_id: int, db: Session = Depends(get_db)):
    src = db.get(ExcelSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        excel_matcher.reload_source(db, src.id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"重建索引失败: {e}") from e
    return {"ok": True, "source": src.to_dict()}


@router.post("/{source_id}/test-match")
def test_match(source_id: int, req: TestMatchRequest, db: Session = Depends(get_db)):
    """用模拟 OCR 字段测试匹配（人工验证列映射是否正确）。"""
    src = db.get(ExcelSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    result = excel_matcher.match(db, req.fields)
    return {"ok": True, "match": result.to_dict()}


@router.delete("/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    src = db.get(ExcelSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    # 删除配置与文件（文件在 data/excel_sources 下，属于系统管理数据）
    path = Path(src.file_path)
    try:
        if path.exists():
            path.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("删除 Excel 源文件失败 %s: %s", path, e)
    db.query(ExcelSheetConfig).filter(ExcelSheetConfig.source_id == src.id).delete()
    db.delete(src)
    db.commit()
    excel_matcher._cache.pop(src.id, None)  # noqa: SLF001
    excel_matcher._cache_sig.pop(src.id, None)  # noqa: SLF001
    return {"ok": True, "message": "已删除"}
