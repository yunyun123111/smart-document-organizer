"""规则模拟器服务（V1.5-05）。

改完规则后，用历史文档"演练"一遍，看匹配效果，不落库、不改任何文件。
测试集 = 所有已有标准答案的文档（document_type 非空且非"其他/未识别"，不限状态），
用真实 document_type 作为"正确答案"，对比规则判定结果，输出命中/误判/准确率。

纯本地零 token：只是拿规则引擎跑一遍历史数据。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Category, Document, FilenameRule, Rule
from backend.services.filename_rule_service import filename_rule_service
from backend.utils.logger import get_logger

logger = get_logger("services.rule_simulator")

# 无标准答案的类型（不能作为测试基准）
_NO_ANSWER_TYPES = ("其他", "未识别", "")

# 分类匹配别名：真实 document_type 与分类名可能因历史原因存在同义差异
_TYPE_ALIASES: dict[str, str] = {}


def _norm_type(t: str | None) -> str:
    """归一化类型名：去空白、全角空格，用于比较。"""
    if not t:
        return ""
    return "".join(t.split()).lower()


def _type_equal(a: str | None, b: str | None) -> bool:
    """判断两个类型名是否一致（支持别名）。"""
    na, nb = _norm_type(a), _norm_type(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return _TYPE_ALIASES.get(na) == nb or _TYPE_ALIASES.get(nb) == na


def _eval_keyword_rules(rules: list[Rule], text: str) -> tuple[str, list[str]] | None:
    """对给定规则列表评估文本，返回 (分类名, 命中关键词列表)。

    与 RuleEngine.evaluate 算法一致：按分类分组，分类得分 = 该分类命中规则 weight 之和，
    取得分最高（同分取 priority 高者）的分类；无命中返回 None。
    """
    if not rules or not text:
        return None
    from backend.models import MATCH_EXACT, MATCH_REGEX

    # 按分类聚合
    scores: dict[int, float] = {}
    keywords_by_cat: dict[int, list[str]] = {}
    priority_by_cat: dict[int, int] = {}
    cat_by_id: dict[int, str] = {}

    for r in rules:
        matched = False
        try:
            if r.match_type == MATCH_REGEX:
                import re

                matched = re.search(r.keyword, text) is not None
            elif r.match_type == MATCH_EXACT:
                matched = r.keyword == text.strip() or r.keyword in text.splitlines()
            else:  # contains
                matched = r.keyword in text
        except Exception:  # noqa: BLE001 非法正则等，跳过该规则
            logger.warning("规则模拟：关键词 '%s' 匹配异常（已跳过）", r.keyword)
            continue
        if not matched:
            continue
        scores[r.category_id] = scores.get(r.category_id, 0.0) + r.weight
        keywords_by_cat.setdefault(r.category_id, []).append(r.keyword)
        priority_by_cat[r.category_id] = max(priority_by_cat.get(r.category_id, 0), r.priority)
        if r.category_id not in cat_by_id:
            cat_by_id[r.category_id] = r.category.name if r.category else str(r.category_id)

    if not scores:
        return None
    # 得分最高 → 同分取 priority 最高 → 再同分取分类 id 小者（稳定）
    best_id = max(
        scores,
        key=lambda cid: (scores[cid], priority_by_cat.get(cid, 0), -cid),
    )
    return cat_by_id[best_id], keywords_by_cat[best_id]


class RuleSimulator:
    """规则模拟：用历史文档测试规则匹配效果（只读）。"""

    def simulate(
        self,
        db: Session,
        rule_type: str,
        rule_ids: list[int] | None = None,
        category_id: int | None = None,
        limit: int = 200,
    ) -> dict:
        """执行模拟。

        入参:
          rule_type: "filename"（文件名规则）| "keyword"（分类关键词规则）
          rule_ids:  指定规则 id 列表（None=全部启用规则）
          category_id: 仅测试绑定到该分类的规则
          limit:     测试集数量上限（最近处理的文件优先）
        返回:
          统计 + 明细（只读，不落库）
        """
        limit = max(1, min(int(limit or 200), 1000))

        # 1. 加载测试集：有标准答案的文档（不限状态）
        docs = (
            db.query(Document)
            .filter(
                Document.document_type.isnot(None),
                Document.document_type != "",
            )
            .order_by(Document.id.desc())
            .limit(limit)
            .all()
        )
        # 排除无标准答案的类型
        docs = [d for d in docs if d.document_type not in _NO_ANSWER_TYPES]

        details: list[dict] = []
        matched = 0
        correct = 0
        unmatched = 0

        if rule_type == "filename":
            q = db.query(FilenameRule).filter(FilenameRule.enabled.is_(True))
            if rule_ids:
                q = q.filter(FilenameRule.id.in_(rule_ids))
            if category_id is not None:
                q = q.filter(FilenameRule.category_id == category_id)
            rules = q.all()
            rule_lookup = {r.id: r for r in rules}
            for doc in docs:
                actual = doc.document_type or ""
                hit = filename_rule_service.match(doc.original_filename, db)
                # 若指定了规则，只认可指定规则命中的结果
                if hit and rule_ids and hit.rule.id not in set(rule_ids):
                    hit = None
                if hit is None:
                    unmatched += 1
                    details.append(
                        {
                            "document_id": doc.id,
                            "filename": doc.original_filename,
                            "actual_type": actual,
                            "predicted_type": "",
                            "matched": False,
                            "correct": None,
                            "keywords": [],
                        }
                    )
                    continue
                matched += 1
                predicted = hit.category_name
                ok = _type_equal(actual, predicted)
                if ok:
                    correct += 1
                details.append(
                    {
                        "document_id": doc.id,
                        "filename": doc.original_filename,
                        "actual_type": actual,
                        "predicted_type": predicted,
                        "matched": True,
                        "correct": ok,
                        "keywords": [hit.rule.pattern],
                    }
                )
            tested_rules = len(rules)
        elif rule_type == "keyword":
            q = db.query(Rule).filter(Rule.enabled.is_(True))
            if rule_ids:
                q = q.filter(Rule.id.in_(rule_ids))
            if category_id is not None:
                q = q.filter(Rule.category_id == category_id)
            rules = q.all()
            for doc in docs:
                actual = doc.document_type or ""
                res = _eval_keyword_rules(rules, doc.extracted_text or "")
                if res is None:
                    unmatched += 1
                    details.append(
                        {
                            "document_id": doc.id,
                            "filename": doc.original_filename,
                            "actual_type": actual,
                            "predicted_type": "",
                            "matched": False,
                            "correct": None,
                            "keywords": [],
                        }
                    )
                    continue
                predicted, keywords = res
                matched += 1
                ok = _type_equal(actual, predicted)
                if ok:
                    correct += 1
                details.append(
                    {
                        "document_id": doc.id,
                        "filename": doc.original_filename,
                        "actual_type": actual,
                        "predicted_type": predicted,
                        "matched": True,
                        "correct": ok,
                        "keywords": keywords,
                    }
                )
            tested_rules = len(rules)
        else:
            raise ValueError(f"不支持的规则类型: {rule_type}（应为 filename / keyword）")

        misclassified = matched - correct
        result = {
            "rule_type": rule_type,
            "tested_rules": tested_rules,
            "total_tested": len(docs),
            "matched": matched,
            "unmatched": unmatched,
            "misclassified": misclassified,
            "hit_accuracy": round(correct / matched, 4) if matched else 0.0,
            "coverage": round(matched / len(docs), 4) if docs else 0.0,
            "details": details,
        }
        logger.info(
            "规则模拟完成: type=%s 测试%d份 命中%d 误判%d 准确率%.1f%%",
            rule_type, len(docs), matched, misclassified,
            result["hit_accuracy"] * 100,
        )
        return result


# 全局单例
rule_simulator = RuleSimulator()
