"""同类文件聚类服务（智能批处理）。

把待人工确认的文件按「合同号 → 公司名 → 模板版式」自动聚合成组，
一次审核可应用到整组。组内每份文件仍保持各自的类型与字段。
"""
from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from backend.utils.logger import get_logger

if TYPE_CHECKING:
    from backend.models.document import Document

logger = get_logger("services.cluster_service")

# 聚类优先级：合同号 > 公司名 > 模板版式
CLUSTER_FIELDS = ("contract_no", "company")


def _fields_dict(doc: "Document") -> dict[str, str]:
    return {f.field_name: f.field_value for f in doc.fields}


def _norm_text(text: str) -> str:
    """归一化文本：去空白/全角空格、转小写，用于版式指纹。"""
    return re.sub(r"[\s\u3000]+", "", text or "").lower()


def text_fingerprint(doc: "Document") -> str:
    """内容指纹：归一化文本的 SHA256 前缀。无文本返回空串。"""
    norm = _norm_text(doc.extracted_text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def cluster_documents(docs: list["Document"]) -> list[dict]:
    """对待确认文档聚类，返回分组结构。

    返回: [{"group_key","group_label","documents":[Document,...]}, ...]
    分组优先级：
      1. 合同号相同（contract_no）
      2. 公司名相同（company）
      3. 模板版式相同（document_type + 内容指纹，≥2 份才成组）
      4. 其余各自单独成组（未分组）
    """
    items = [(d, _fields_dict(d)) for d in docs]
    groups: list[dict] = []
    assigned: set[int] = set()

    def _add(key: str, label: str, lst: list["Document"]) -> None:
        groups.append({"group_key": key, "group_label": label, "documents": lst})
        assigned.update(d.id for d in lst)

    # 1) 合同号
    by_contract: dict[str, list] = {}
    for d, fields in items:
        no = (fields.get("contract_no") or "").strip()
        if no:
            by_contract.setdefault(no, []).append(d)
    for no, lst in by_contract.items():
        _add(f"contract:{no}", f"合同号 {no}", lst)

    # 2) 公司名
    rest = [t for t in items if t[0].id not in assigned]
    by_company: dict[str, list] = {}
    for d, fields in rest:
        co = (fields.get("company") or "").strip()
        if co:
            by_company.setdefault(co, []).append(d)
    for co, lst in by_company.items():
        _add(f"company:{co}", f"公司 {co}", lst)

    # 3) 模板版式（document_type + 内容指纹，同版式 ≥2 份）
    rest = [t for t in items if t[0].id not in assigned]
    by_tpl: dict[tuple, list] = {}
    for d, _fields in rest:
        fp = text_fingerprint(d)
        if not fp:
            continue
        key = (d.document_type or "未识别", fp)
        by_tpl.setdefault(key, []).append(d)
    for (t, fp), lst in by_tpl.items():
        if len(lst) >= 2:
            _add(f"tpl:{t}:{fp[:6]}", f"同类 {t}", lst)

    # 4) 未分组
    rest = [t for t in items if t[0].id not in assigned]
    for d, _fields in rest:
        _add(f"single:{d.id}", "未分组", [d])

    logger.info("聚类完成: 共 %d 份文档 -> %d 组", len(docs), len(groups))
    return groups
