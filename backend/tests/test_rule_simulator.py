"""规则模拟器测试：用历史文档测试规则匹配效果（只读）。"""
from __future__ import annotations

import pytest

from backend.database import seed_default_categories
from backend.models import (
    STATUS_ARCHIVED,
    STATUS_NEED_REVIEW,
    Category,
    Document,
    FilenameRule,
    Rule,
)
from backend.services.rule_simulator import rule_simulator


@pytest.fixture(autouse=True)
def _seed_cats(db):
    """每个用例先写默认分类（conftest 只建表，不写默认数据/默认规则）。"""
    seed_default_categories(db)
    yield


def _make_doc(
    db,
    filename: str,
    doc_type: str,
    text: str = "",
    status: str = STATUS_ARCHIVED,
) -> Document:
    d = Document(
        original_filename=filename,
        current_filename=filename,
        original_path="/tmp/" + filename,
        current_path="/tmp/" + filename,
        file_type="pdf",
        mime_type="application/pdf",
        file_size=1024,
        document_type=doc_type,
        extracted_text=text,
        status=status,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _get_cat(db, name: str) -> Category:
    cat = db.query(Category).filter(Category.name == name).first()
    assert cat is not None, f"分类 {name} 不存在"
    return cat


class TestFilenameSimulate:
    def test_basic_hit_and_miss(self, db):
        """文件名规则：命中判对 / 未命中 统计正确。"""
        cat = _get_cat(db, "销售合同")
        _make_doc(db, "SJWLXS（DD）-2026-YC0453.pdf", "销售合同")
        _make_doc(db, "随便一个文件.pdf", "发票")

        db.add(FilenameRule(category_id=cat.id, pattern=r"SJWLXS", note="测试"))
        db.commit()

        res = rule_simulator.simulate(db, rule_type="filename")
        assert res["total_tested"] == 2
        assert res["matched"] == 1
        assert res["unmatched"] == 1
        assert res["misclassified"] == 0
        assert res["hit_accuracy"] == 1.0
        assert res["coverage"] == 0.5

    def test_misclassification_detected(self, db):
        """文件名规则：命中但分类判错 → 误判。"""
        cat = _get_cat(db, "采购合同")
        _make_doc(db, "SJWLXS（DD）-2026-YC0453.pdf", "销售合同")

        db.add(FilenameRule(category_id=cat.id, pattern=r"SJWLXS", note="错绑"))
        db.commit()

        res = rule_simulator.simulate(db, rule_type="filename")
        assert res["total_tested"] == 1
        assert res["matched"] == 1
        assert res["misclassified"] == 1
        assert res["hit_accuracy"] == 0.0

    def test_rule_ids_filter(self, db):
        """指定规则 id：只统计该规则。"""
        cat1 = _get_cat(db, "销售合同")
        cat2 = _get_cat(db, "采购合同")
        _make_doc(db, "SJWLCG（DD）-2026-YC0532.pdf", "采购合同")

        r1 = FilenameRule(category_id=cat1.id, pattern=r"SJWLXS", note="a")
        r2 = FilenameRule(category_id=cat2.id, pattern=r"SJWLCG", note="b")
        db.add_all([r1, r2])
        db.commit()

        # 只测 r1（不匹配该文件）→ 未命中
        res = rule_simulator.simulate(db, rule_type="filename", rule_ids=[r1.id])
        assert res["total_tested"] == 1
        assert res["matched"] == 0
        assert res["unmatched"] == 1

        # 只测 r2（命中且判对）
        res2 = rule_simulator.simulate(db, rule_type="filename", rule_ids=[r2.id])
        assert res2["total_tested"] == 1
        assert res2["matched"] == 1
        assert res2["misclassified"] == 0


class TestKeywordSimulate:
    def test_basic_hit(self, db):
        """关键词规则：文本命中判对。"""
        cat = _get_cat(db, "结算单")
        _make_doc(db, "上游结算.pdf", "结算单", text="本合同为煤炭结算单，数量5000吨")
        db.add(Rule(category_id=cat.id, keyword="结算单", match_type="contains"))
        db.commit()

        res = rule_simulator.simulate(db, rule_type="keyword")
        assert res["total_tested"] == 1
        assert res["matched"] == 1
        assert res["misclassified"] == 0
        assert res["hit_accuracy"] == 1.0

    def test_no_rules_no_match(self, db):
        """无启用规则：全部未命中，不报错。"""
        _make_doc(db, "a.pdf", "发票", text="一些文本")
        res = rule_simulator.simulate(db, rule_type="keyword")
        assert res["total_tested"] == 1
        assert res["matched"] == 0
        assert res["unmatched"] == 1
        assert res["hit_accuracy"] == 0.0

    def test_category_filter(self, db):
        """按分类过滤规则。"""
        cat1 = _get_cat(db, "结算单")
        cat2 = _get_cat(db, "发票")
        _make_doc(db, "结算.pdf", "结算单", text="结算单内容")
        db.add_all([
            Rule(category_id=cat1.id, keyword="结算单", match_type="contains"),
            Rule(category_id=cat2.id, keyword="结算单", match_type="contains"),
        ])
        db.commit()

        res = rule_simulator.simulate(db, rule_type="keyword", category_id=cat2.id)
        # 规则只测发票分类的，但该文件真实类型是结算单 → 误判
        assert res["total_tested"] == 1
        assert res["matched"] == 1
        assert res["misclassified"] == 1


class TestTestSetSelection:
    def test_includes_need_review_docs(self, db):
        """测试集包含待审核文档（只要有标准答案）。"""
        cat = _get_cat(db, "销售合同")
        _make_doc(db, "a.pdf", "销售合同", status=STATUS_ARCHIVED)
        _make_doc(db, "b.pdf", "销售合同", status=STATUS_NEED_REVIEW)
        db.add(FilenameRule(category_id=cat.id, pattern=r"a\.pdf"))
        db.commit()

        res = rule_simulator.simulate(db, rule_type="filename")
        assert res["total_tested"] == 2  # 两个状态的文档都被纳入

    def test_excludes_no_answer_types(self, db):
        """无标准答案（其他/未识别/空）的文档不进测试集。"""
        cat = _get_cat(db, "销售合同")
        _make_doc(db, "good.pdf", "销售合同")
        _make_doc(db, "bad1.pdf", "其他")
        _make_doc(db, "bad2.pdf", "未识别")
        _make_doc(db, "bad3.pdf", "")
        db.add(FilenameRule(category_id=cat.id, pattern=r"good"))
        db.commit()

        res = rule_simulator.simulate(db, rule_type="filename")
        assert res["total_tested"] == 1

    def test_empty_db(self, db):
        """空库不报错。"""
        res = rule_simulator.simulate(db, rule_type="filename")
        assert res["total_tested"] == 0
        assert res["matched"] == 0
        assert res["hit_accuracy"] == 0.0

    def test_readonly_no_side_effect(self, db):
        """模拟不修改任何数据。"""
        cat = _get_cat(db, "销售合同")
        _make_doc(db, "SJWLXS.pdf", "销售合同")
        db.add(FilenameRule(category_id=cat.id, pattern=r"SJWLXS"))
        db.commit()
        before = db.query(Document).count() + db.query(FilenameRule).count()
        rule_simulator.simulate(db, rule_type="filename")
        after = db.query(Document).count() + db.query(FilenameRule).count()
        assert before == after

    def test_invalid_rule_type(self, db):
        """非法规则类型报错。"""
        with pytest.raises(ValueError):
            rule_simulator.simulate(db, rule_type="unknown")
