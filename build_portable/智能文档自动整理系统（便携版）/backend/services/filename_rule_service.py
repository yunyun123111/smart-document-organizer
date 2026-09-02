"""文件名规则服务：按文件名直接归档（省 token 快速通道）。

业务编码明确的文件（如 SJWLXS=销售合同、SJWLCG=采购合同）命中即归档，
跳过解析 / OCR / 规则 / AI，实现零消耗直接归档。
"""
from __future__ import annotations

import re
from typing import NamedTuple

from sqlalchemy.orm import Session

from backend.models import Category, FilenameRule
from backend.utils.logger import get_logger

logger = get_logger("services.filename_rule_service")


class FilenameMatch(NamedTuple):
    """文件名命中结果。"""
    category: Category
    rule: FilenameRule

    @property
    def category_name(self) -> str:
        return self.category.name

    @property
    def category_path(self) -> str:
        return self.category.path


class FilenameRuleService:
    """按文件名匹配分类。"""

    def match(self, filename: str, db: Session) -> FilenameMatch | None:
        """返回第一个命中的规则（按 priority 降序）。未命中返回 None。"""
        if not filename:
            return None
        rules = (
            db.query(FilenameRule)
            .filter(FilenameRule.enabled.is_(True))
            .order_by(FilenameRule.priority.desc(), FilenameRule.id.asc())
            .all()
        )
        for rule in rules:
            try:
                if re.search(rule.pattern, filename, re.IGNORECASE):
                    cat = rule.category
                    if cat is not None and cat.enabled:
                        return FilenameMatch(category=cat, rule=rule)
            except re.error:
                logger.warning("文件名规则正则非法，跳过: %r", rule.pattern)
        return None


# 全局单例
filename_rule_service = FilenameRuleService()
