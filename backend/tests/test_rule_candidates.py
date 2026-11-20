"""P1-3 候选规则（方案 A：文件名前缀）回归测试。

覆盖：
- 前缀提取：业务编码前缀可取，OA 编号前缀不可取
- 人工改判分类 → 生成 pending 候选；命中不足 / 未改判 → 不生成
- 已有等价 FilenameRule → 不生成
- 采纳 → 生成启用中的 FilenameRule；拒绝 → 状态 rejected
- API：列表默认只给 pending
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import (
    CANDIDATE_ADOPTED,
    CANDIDATE_PENDING,
    CANDIDATE_REJECTED,
    STATUS_NEED_REVIEW,
    Category,
    Document,
    DocumentField,
    FilenameRule,
    RuleCandidate,
)
from backend.services.rule_candidate_service import (
    adopt_candidate,
    extract_prefix,
    reject_candidate,
)

CONTRACT_TEXT = (
    "销售合同\n"
    "合同编号：HSYCXS-2026-0001\n"
    "甲方：ABC有限公司\n"
    "乙方：DEF有限公司\n"
    "合同金额：人民币 128,500.00 元\n"
    "签订日期：2026年08月20日\n"
    "货物名称：铁矿粉 湿吨 车板交货\n"
    "本合同由甲乙双方根据《中华人民共和国民法典》订立，双方应严格履行。\n"
)

TARGET = "合同/采购合同"
SOURCE = "合同/销售合同"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    with TestClient(app) as c:
        yield c


def _ensure_category(db: Session, path: str, name: str) -> Category:
    """取已有分类，没有才建（默认分类种子已在库里，别重复造）。"""
    cat = db.query(Category).filter(Category.path == path).first()
    if cat is None:
        cat = Category(name=name, path=path)
        db.add(cat)
        db.commit()
        db.refresh(cat)
    return cat


def _add_doc(
    db: Session,
    root,
    *,
    name: str,
    text: str = CONTRACT_TEXT,
    category: str = SOURCE,
) -> Document:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(f"%PDF-1.4 fake content of {name}".encode("utf-8"))
    doc = Document(
        original_filename=name,
        current_filename=name,
        original_path=str(p),
        current_path=str(p),
        file_type="pdf",
        document_type="销售合同",
        status=STATUS_NEED_REVIEW,
        confidence=0.7,
        extracted_text=text,
    )
    db.add(doc)
    db.flush()
    db.add(
        DocumentField(
            document_id=doc.id, field_name="suggested_category",
            field_value=category, confidence=1.0,
        )
    )
    db.commit()
    return doc


class TestExtractPrefix:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("SJWLXS（DD）-2026-YC0001.txt", "SJWLXS"),
            ("HSYCCG-0322.pdf", "HSYCCG"),
            ("HSYCXS-0319 结算单.pdf", "HSYCXS"),
            # OA / 业务线编号带 8 位以上数字，不能当前缀
            ("SOTC202610210002.pdf", None),
            ("BOTC202610220001.pdf", None),
            ("YWX202610220008.pdf", None),
            # 没有业务编码
            ("放货通知 法瑞5000吨.pdf", None),
            ("", None),
        ],
    )
    def test_extract(self, name, expected):
        assert extract_prefix(name) == expected


class TestCandidateGeneration:
    def test_category_change_creates_candidate(self, client, db, tmp_path):
        # 同前缀 3 份：2 份已确认到目标分类，1 份待改判
        for i in range(2):
            _add_doc(db, tmp_path, name=f"TESTXS-2026-000{i}.pdf", category=TARGET)
        changed = _add_doc(db, tmp_path, name="TESTXS-2026-0002.pdf", category=SOURCE)

        assert client.post(
            f"/api/review/{changed.id}/approve", json={"category_path": TARGET}
        ).status_code == 200

        with SessionLocal() as s:
            cands = s.query(RuleCandidate).all()
            assert len(cands) == 1
            c = cands[0]
            assert c.keyword == "TESTXS"
            assert c.category_path == TARGET
            assert c.status == CANDIDATE_PENDING
            assert c.from_doc_id == changed.id
            assert c.hit_total == 3
            # 已归档的两份 + 本次确认的这份都在目标分类
            assert c.hit_target == 3

    def test_no_candidate_when_suggestion_unchanged(self, client, db, tmp_path):
        d1 = _add_doc(db, tmp_path, name="TESTXS-2026-0001.pdf", category=SOURCE)
        _add_doc(db, tmp_path, name="TESTXS-2026-0002.pdf", category=SOURCE)

        assert client.post(
            f"/api/review/{d1.id}/approve", json={"category_path": SOURCE}
        ).status_code == 200

        with SessionLocal() as s:
            assert s.query(RuleCandidate).count() == 0

    def test_no_candidate_for_single_hit(self, client, db, tmp_path):
        only = _add_doc(db, tmp_path, name="TESTXS-2026-0001.pdf", category=SOURCE)

        assert client.post(
            f"/api/review/{only.id}/approve", json={"category_path": TARGET}
        ).status_code == 200

        with SessionLocal() as s:
            assert s.query(RuleCandidate).count() == 0

    def test_no_candidate_when_rule_exists(self, client, db, tmp_path):
        cat = _ensure_category(db, TARGET, "采购合同")
        db.add(FilenameRule(category_id=cat.id, pattern="TESTXS", priority=0))
        db.commit()

        _add_doc(db, tmp_path, name="TESTXS-2026-0002.pdf", category=TARGET)
        changed = _add_doc(db, tmp_path, name="TESTXS-2026-0001.pdf", category=SOURCE)
        client.post(f"/api/review/{changed.id}/approve", json={"category_path": TARGET})

        with SessionLocal() as s:
            assert s.query(RuleCandidate).count() == 0

    def test_no_candidate_when_prefix_bound_to_other_category(self, client, db, tmp_path):
        """同前缀已有规则指向别的分类 → 不再为个例造第二条规则。"""
        cat = _ensure_category(db, SOURCE, "销售合同")
        db.add(FilenameRule(category_id=cat.id, pattern="TESTXS", priority=0))
        db.commit()

        _add_doc(db, tmp_path, name="TESTXS-2026-0002.pdf", category=TARGET)
        changed = _add_doc(db, tmp_path, name="TESTXS-2026-0001.pdf", category=SOURCE)
        client.post(f"/api/review/{changed.id}/approve", json={"category_path": TARGET})

        with SessionLocal() as s:
            assert s.query(RuleCandidate).count() == 0

    def test_duplicate_change_does_not_duplicate_candidate(self, client, db, tmp_path):
        _add_doc(db, tmp_path, name="TESTXS-2026-0001.pdf", category=TARGET)
        a = _add_doc(db, tmp_path, name="TESTXS-2026-0002.pdf", category=SOURCE)
        b = _add_doc(db, tmp_path, name="TESTXS-2026-0003.pdf", category=SOURCE)

        client.post(f"/api/review/{a.id}/approve", json={"category_path": TARGET})
        client.post(f"/api/review/{b.id}/approve", json={"category_path": TARGET})

        with SessionLocal() as s:
            assert s.query(RuleCandidate).count() == 1


class TestAdoptReject:
    def _candidate(self, db: Session) -> RuleCandidate:
        _ensure_category(db, TARGET, "采购合同")
        cand = RuleCandidate(keyword="ZZTEST", category_path=TARGET, hit_total=3, hit_target=2)
        db.add(cand)
        db.commit()
        db.refresh(cand)
        return cand

    def test_adopt_creates_enabled_rule(self, db):
        cand = self._candidate(db)
        rule = adopt_candidate(db, cand)

        assert rule.pattern == "ZZTEST"
        assert rule.enabled is True
        assert cand.status == CANDIDATE_ADOPTED
        assert cand.adopted_rule_id == rule.id

    def test_adopt_twice_rejected(self, db):
        cand = self._candidate(db)
        adopt_candidate(db, cand)
        with pytest.raises(ValueError):
            adopt_candidate(db, cand)

    def test_adopt_missing_category(self, db):
        cand = RuleCandidate(keyword="ZZZZ", category_path="不存在的分类", hit_total=2)
        db.add(cand)
        db.commit()
        db.refresh(cand)
        with pytest.raises(ValueError, match="目标分类不存在"):
            adopt_candidate(db, cand)

    def test_reject_marks_status(self, db):
        cand = self._candidate(db)
        reject_candidate(db, cand)
        assert cand.status == CANDIDATE_REJECTED
        with pytest.raises(ValueError):
            reject_candidate(db, cand)


class TestApi:
    def test_list_defaults_to_pending(self, db):
        db.add_all([
            RuleCandidate(keyword="AAAA", category_path=TARGET, status=CANDIDATE_PENDING),
            RuleCandidate(keyword="BBBB", category_path=TARGET, status=CANDIDATE_REJECTED),
        ])
        db.commit()

        with TestClient(app) as c:
            r = c.get("/api/rule-candidates")
            assert r.status_code == 200
            assert [x["keyword"] for x in r.json()] == ["AAAA"]

    def test_adopt_via_api(self, db):
        _ensure_category(db, TARGET, "采购合同")
        cand = RuleCandidate(keyword="CCCC", category_path=TARGET)
        db.add(cand)
        db.commit()
        cand_id = cand.id

        with TestClient(app) as c:
            r = c.post(f"/api/rule-candidates/{cand_id}/adopt")
            assert r.status_code == 200, r.text
            assert r.json()["pattern"] == "CCCC"
            assert r.json()["enabled"] is True

