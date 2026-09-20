# -*- coding: utf-8 -*-
"""归档模板预设管理：用户可自由添加/删除的模板库（本地 JSON 存储）。

首次使用自动初始化内置建议模板；之后完全由用户管理。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.config import BASE_DIR

logger = logging.getLogger("sdo.archive_template")

_TEMPLATES_FILE = BASE_DIR / "data" / "archive_templates.json"

#: 首次初始化时的内置建议模板
DEFAULT_TEMPLATES = [
    {"value": "category/year/month", "label": "分类 / 年 / 月（默认）"},
    {"value": "category/customer/contract_no", "label": "分类 / 客户名 / 合同编号"},
    {"value": "category/project_no/date", "label": "分类 / 项目编号 / 日期"},
    {"value": "category/contract_no", "label": "分类 / 合同编号"},
    {"value": "category/ship_name/year", "label": "分类 / 船名 / 年份"},
]


def _load_raw() -> list[dict]:
    if not _TEMPLATES_FILE.exists():
        _save_raw(DEFAULT_TEMPLATES)
        return [dict(t) for t in DEFAULT_TEMPLATES]
    try:
        data = json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
        items = data.get("templates", []) if isinstance(data, dict) else data
        return [dict(t) for t in items if isinstance(t, dict) and t.get("value")]
    except Exception:  # noqa: BLE001
        logger.exception("读取归档模板预设失败，回退默认")
        return [dict(t) for t in DEFAULT_TEMPLATES]


def _save_raw(items: list[dict]) -> None:
    _TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TEMPLATES_FILE.write_text(
        json.dumps({"templates": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_templates() -> list[dict]:
    """返回全部预设模板。"""
    return _load_raw()


def add_template(value: str, label: str | None = None) -> list[dict]:
    """新增预设；同名 value 已存在时更新 label，返回最新列表。"""
    value = (value or "").strip()
    if not value:
        raise ValueError("模板不能为空")
    if value == "__custom__":
        raise ValueError("保留关键字，不能作为模板")
    items = _load_raw()
    label = (label or value).strip()
    for t in items:
        if t["value"] == value:
            t["label"] = label
            _save_raw(items)
            return items
    items.append({"value": value, "label": label})
    _save_raw(items)
    return items


def remove_template(value: str) -> list[dict]:
    """删除预设；返回最新列表。"""
    value = (value or "").strip()
    items = _load_raw()
    kept = [t for t in items if t["value"] != value]
    if len(kept) != len(items):
        _save_raw(kept)
    return kept
