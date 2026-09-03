"""格式样本（自动学习）测试：指纹提取、版式匹配、样本 API。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.models import DocumentSample
from backend.services.sample_service import (
    SAMPLE_MATCH_THRESHOLD,
    build_fingerprint,
    match_score,
    sample_service,
)

CONTRACT_TEXT = (
    "销售合同\n"
    "合同编号：XS202608001\n"
    "甲方：ABC有限公司\n"
    "乙方：DEF有限公司\n"
    "船名：OCEANSTAR/快空\n"
    "货物名称：铁矿粉\n"
    "数量：10000湿吨\n"
    "单价：688元/湿吨\n"
    "合同金额：人民币捌佰陆拾万元整\n"
    "交货方式：国内港口车板交货。\n"
    "违约责任：任何一方违约需赔偿损失。\n"
    "本合同由甲乙双方根据《中华人民共和国民法典》订立。\n"
)

INVOICE_TEXT = (
    "增值税专用发票\n"
    "发票号码：3100234567\n"
    "开票日期：2026年08月20日\n"
    "购买方：ABC有限公司\n"
    "销售方：DEF有限公司\n"
    "货物名称：铁矿粉\n"
    "数量：100吨\n"
    "单价：688元\n"
    "金额：68800.00\n"
    "税率：13%\n"
    "税额：8944.00\n"
)


class TestFingerprint:
    def test_build_fingerprint_has_features(self):
        fp = build_fingerprint(CONTRACT_TEXT)
        assert "labels" in fp and fp["labels"]
        assert "grams" in fp and fp["grams"]
        assert "fields" in fp
        assert "stats" in fp
        # 合同特有标签词应被捕获
        assert any("合同" in w or "编号" in w for w in fp["labels"])
        # 高频业务 2-gram（重复出现的"合同"）应保留
        assert "合同" in fp["grams"]

    def test_match_same_layout_high(self):
        fp = build_fingerprint(CONTRACT_TEXT)
        fields = [f.name for f in __import__(
            "backend.services.field_extractor", fromlist=["field_extractor"]
        ).field_extractor.extract(CONTRACT_TEXT)]
        score = match_score(fp, CONTRACT_TEXT, fields)
        assert score >= SAMPLE_MATCH_THRESHOLD

    def test_match_different_doc_low(self):
        fp = build_fingerprint(CONTRACT_TEXT)
        fields = [f.name for f in __import__(
            "backend.services.field_extractor", fromlist=["field_extractor"]
        ).field_extractor.extract(INVOICE_TEXT)]
        score = match_score(fp, INVOICE_TEXT, fields)
        assert score < SAMPLE_MATCH_THRESHOLD


class TestMatchAll:
    def test_match_all_hit(self, db):
        from backend.database import seed_default_categories

        seed_default_categories(db)
        fp = build_fingerprint(CONTRACT_TEXT)
        s = DocumentSample(
            document_type="销售合同",
            category_path="合同/销售合同",
            original_filename="样本.pdf",
            fingerprint=__import__("json").dumps(fp, ensure_ascii=False),
        )
        db.add(s)
        db.commit()
        fields = [f.name for f in __import__(
            "backend.services.field_extractor", fromlist=["field_extractor"]
        ).field_extractor.extract(CONTRACT_TEXT)]
        best, score = sample_service.match_all(db, CONTRACT_TEXT, fields)
        assert best is not None
        assert best.document_type == "销售合同"
        assert score >= SAMPLE_MATCH_THRESHOLD

    def test_match_all_miss(self, db):
        fp = build_fingerprint(CONTRACT_TEXT)
        db.add(
            DocumentSample(
                document_type="销售合同",
                category_path="合同/销售合同",
                original_filename="样本.pdf",
                fingerprint=__import__("json").dumps(fp, ensure_ascii=False),
            )
        )
        db.commit()
        fields = [f.name for f in __import__(
            "backend.services.field_extractor", fromlist=["field_extractor"]
        ).field_extractor.extract(INVOICE_TEXT)]
        best, score = sample_service.match_all(db, INVOICE_TEXT, fields)
        assert best is None
        assert score < SAMPLE_MATCH_THRESHOLD


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


from pathlib import Path  # noqa: E402


class TestSampleAPI:
    def test_create_list_patch_delete(self, client):
        # 上传 txt 样本（可解析，不触发 OCR）
        r = client.post(
            "/api/samples",
            files={"file": ("合同样本.txt", CONTRACT_TEXT.encode("utf-8"), "text/plain")},
            data={"document_type": "销售合同", "category_path": "合同/销售合同"},
        )
        assert r.status_code == 200, r.text
        item = r.json()
        assert item["document_type"] == "销售合同"
        assert item["category_path"] == "合同/销售合同"
        assert item["summary"]["fields"]
        sid = item["id"]

        r = client.get("/api/samples")
        assert r.status_code == 200
        assert any(x["id"] == sid for x in r.json())

        # 停用
        r = client.patch(f"/api/samples/{sid}", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

        # 删除
        r = client.delete(f"/api/samples/{sid}")
        assert r.status_code == 200
        r = client.get("/api/samples")
        assert all(x["id"] != sid for x in r.json())

    def test_create_invalid_file(self, client):
        # 无法解析的文件 -> 422
        r = client.post(
            "/api/samples",
            files={"file": ("坏文件.pdf", b"%PDF-bad", "application/pdf")},
            data={"document_type": "销售合同", "category_path": "合同/销售合同"},
        )
        assert r.status_code in (400, 422)
