"""规则引擎（规格书第二十三/二十四节）。

职责：
- 关键词匹配（contains / exact / regex）
- 分类优先级与规则权重
- 多个关键词同时命中时得分累加，置信度随之提高

规则优先级链（规格书）：高优先级规则 → 普通规则 → 关键词 → AI。
本引擎只做"规则"部分；AI 兜底由上层 AIService 处理。

业务代码只调用 RuleEngine；规则来源统一从数据库读取（可配置）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.models import Category, MATCH_CONTAINS, MATCH_EXACT, MATCH_REGEX, Rule
from backend.utils.logger import get_logger

logger = get_logger("services.rule_engine")


@dataclass
class RuleMatch:
    """一次规则匹配的结果（一个候选分类）。"""
    category_id: int
    category_name: str
    category_path: str
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    priority_hits: int = 0  # 高优先级规则命中数


def _match_text(text: str, match_type: str, keyword: str) -> bool:
    """按匹配类型判断 keyword 是否命中 text。"""
    if not text:
        return False
    try:
        if match_type == MATCH_CONTAINS:
            return keyword in text
        if match_type == MATCH_EXACT:
            return keyword == text.strip() or keyword in text.splitlines()
        if match_type == MATCH_REGEX:
            return re.search(keyword, text) is not None
    except re.error as e:
        logger.warning("非法正则 '%s': %s", keyword, e)
        return False
    return False


class RuleEngine:
    """规则引擎：从数据库加载启用规则，对文本评估出候选分类。"""

    def __init__(self, db: Session):
        self._rules_by_category: dict[int, list[Rule]] = {}
        self._category_info: dict[int, Category] = {}
        self._load(db)

    def _load(self, db: Session) -> None:
        """加载所有启用规则并按分类分组。"""
        rules = (
            db.query(Rule)
            .filter(Rule.enabled.is_(True))
            .order_by(Rule.priority.desc(), Rule.weight.desc())
            .all()
        )
        for r in rules:
            self._rules_by_category.setdefault(r.category_id, []).append(r)
            if r.category_id not in self._category_info:
                self._category_info[r.category_id] = r.category
        logger.info("规则引擎加载 %d 条规则，覆盖 %d 个分类", len(rules), len(self._rules_by_category))

    def has_rules(self) -> bool:
        return bool(self._rules_by_category)

    def evaluate(self, text: str) -> list[RuleMatch]:
        """对文本进行评估，返回按得分降序的候选分类列表。"""
        text = text or ""
        results: dict[int, RuleMatch] = {}

        for category_id, rules in self._rules_by_category.items():
            cat = self._category_info.get(category_id)
            if cat is None:
                continue
            matched: list[str] = []
            score = 0.0
            priority_hits = 0
            for rule in rules:
                if _match_text(text, rule.match_type, rule.keyword):
                    matched.append(rule.keyword)
                    score += rule.weight
                    if rule.priority >= 50:  # 高优先级规则
                        priority_hits += 1
            if matched:
                results[category_id] = RuleMatch(
                    category_id=category_id,
                    category_name=cat.name,
                    category_path=cat.path,
                    score=round(score, 3),
                    matched_keywords=matched,
                    priority_hits=priority_hits,
                )

        ordered = sorted(results.values(), key=lambda m: (m.score, m.priority_hits), reverse=True)
        return ordered

    def evaluate_best(self, text: str, min_score: float = 0.0) -> RuleMatch | None:
        """返回得分最高的匹配；无匹配或低于 min_score 返回 None。"""
        results = self.evaluate(text)
        if not results:
            return None
        best = results[0]
        if best.score < min_score:
            return None
        return best


def rule_confidence(match: RuleMatch) -> float:
    """把规则匹配结果映射到 0-1 置信度（供置信度系统使用）。

    基础分按命中关键词条数经验映射：1 条 ~0.6，2 条 ~0.75，3 条以上趋于 0.9+。
    在此之上叠加两项加成，让规则表里的 weight / priority 真正影响判定：
    - 权重加成：累加得分 score 超过命中条数（说明存在 weight>1 的规则）时按差值加成；
    - 优先级加成：命中高优先级规则（priority>=50，见 RuleEngine.evaluate）每条 +0.04。
    默认种子数据为 weight=1.0 / priority=10，两项加成均为 0，行为与历史一致。
    """
    n = len(match.matched_keywords)
    if n <= 0:
        return 0.0
    if n == 1:
        base = 0.6
    elif n == 2:
        base = 0.75
    elif n == 3:
        base = 0.85
    else:
        base = min(0.95, 0.85 + 0.03 * (n - 3))

    score = float(getattr(match, "score", 0.0) or 0.0)
    priority_hits = int(getattr(match, "priority_hits", 0) or 0)
    weight_bonus = min(0.05, max(0.0, (score - n) * 0.02))
    priority_bonus = 0.04 * min(2, max(0, priority_hits))
    return round(min(0.98, base + weight_bonus + priority_bonus), 4)
