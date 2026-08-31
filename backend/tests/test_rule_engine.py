"""Phase 5 规则引擎测试：关键词/精确/正则/优先级/权重/多关键词累加。"""
from __future__ import annotations

from backend.database import seed_default_categories, seed_default_rules
from backend.models import Category, Rule
from backend.services.rule_engine import RuleEngine, rule_confidence


def _setup_categories(db):
    """建三个测试分类 + 规则。"""
    cat = Category(name="销售合同", path="合同/销售合同", sort_order=0)
    cat.rules.extend(
        [
            Rule(keyword="销售合同", match_type="contains", priority=50, weight=2.0),
            Rule(keyword="合同编号", match_type="contains", priority=10, weight=1.0),
            Rule(keyword="销售方", match_type="contains", priority=10, weight=1.0),
        ]
    )
    inv = Category(name="发票", path="财务/发票", sort_order=1)
    inv.rules.extend(
        [
            Rule(keyword="增值税专用发票", match_type="contains", priority=10, weight=1.0),
            Rule(keyword="发票号码", match_type="regex", priority=10, weight=1.0),
            Rule(keyword="NOTES", match_type="exact", priority=5, weight=0.5),
        ]
    )
    bank = Category(name="银行回单", path="财务/银行回单", sort_order=2)
    bank.rules.append(Rule(keyword="银行电子回单", match_type="contains", priority=10, weight=1.0))
    db.add_all([cat, inv, bank])
    db.commit()
    return cat, inv, bank


class TestRuleEngine:
    def test_contains_match(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        results = engine.evaluate("这是一份销售合同，合同编号为XS001")
        assert results, "应命中销售合同分类"
        best = results[0]
        assert best.category_name == "销售合同"
        assert "销售合同" in best.matched_keywords
        assert "合同编号" in best.matched_keywords

    def test_multiple_keywords_increase_score(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        single = engine.evaluate("这份文档提到销售合同")
        multi = engine.evaluate("销售合同 合同编号XS001 销售方为ABC公司")
        assert multi[0].score > single[0].score, "多关键词应得分更高"

    def test_regex_match(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        results = engine.evaluate("发票号码：1234567890，增值税专用发票")
        assert results
        assert results[0].category_name == "发票"

    def test_exact_match(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        # exact 匹配整行 "NOTES"
        results = engine.evaluate("NOTES")
        inv = next((r for r in results if r.category_name == "发票"), None)
        assert inv is not None, "exact 命中应归属发票分类"

    def test_priority_ranking(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        # 文本同时含销售合同(priority50,weight2) 和 发票关键词，销售合同得分更高
        text = "销售合同 增值税专用发票 发票号码 8888"
        results = engine.evaluate(text)
        assert results[0].category_name == "销售合同"

    def test_no_match(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        assert engine.evaluate("完全无关的文本内容测试") == []

    def test_evaluate_best(self, db):
        _setup_categories(db)
        engine = RuleEngine(db)
        best = engine.evaluate_best("银行电子回单 交易金额 5000")
        assert best is not None
        assert best.category_name == "银行回单"

    def test_invalid_regex_ignored(self, db):
        cat = Category(name="测试", path="测试")
        cat.rules.append(Rule(keyword="[", match_type="regex", priority=10, weight=1.0))
        db.add(cat)
        db.commit()
        engine = RuleEngine(db)
        assert engine.evaluate("随便什么") == []  # 非法正则不应崩溃


class TestDefaultRules:
    def test_seed_rules(self, db):
        seed_default_categories(db)
        n = seed_default_rules(db)
        assert n > 0

        # 幂等
        n2 = seed_default_rules(db)
        assert n2 == 0

    def test_default_rules_work(self, db):
        seed_default_categories(db)
        seed_default_rules(db)
        engine = RuleEngine(db)
        results = engine.evaluate("增值税普通发票 发票号码123456 开票日期2026-08-20")
        assert results
        assert results[0].category_name == "发票"


class TestRuleConfidence:
    def test_confidence_map(self):
        class _M:
            def __init__(self, n):
                self.matched_keywords = list(range(n))

        assert rule_confidence(_M(0)) == 0.0
        assert rule_confidence(_M(1)) == 0.6
        assert rule_confidence(_M(2)) == 0.75
        assert rule_confidence(_M(3)) == 0.85
        assert rule_confidence(_M(5)) > 0.85
        assert rule_confidence(_M(10)) <= 0.95
