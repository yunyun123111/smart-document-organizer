"""正文全文检索（FTS5 trigram）回归测试。

覆盖：
- 索引创建与迁移记录
- 3 字及以上中文关键词命中（trigram）
- 2 字短词降级扫描（instr）
- 写入/更新触发器同步索引（改正文后旧词失效、新词可搜）
- 命中片段：标记 + HTML 转义
- API 层：keyword 搜索能命中正文并返回片段
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import SessionLocal, _migrate_create_fulltext_index
from backend.models import Document
from backend.services.document_search import (
    fulltext_doc_ids,
    fulltext_snippets,
)


@pytest.fixture
def fts_db(db):
    """建好全文索引的会话（模拟启动迁移）。"""
    _migrate_create_fulltext_index(db)
    return db


def _add_doc(db, text_value: str, *, name: str = "结算单.pdf") -> Document:
    doc = Document(
        original_filename=name,
        current_filename=name,
        original_path=f"/tmp/{name}",
        current_path=f"/tmp/{name}",
        file_type="pdf",
        file_size=100,
        document_type="",
        title="",
        status="pending",
        extracted_text=text_value,
    )
    db.add(doc)
    db.commit()
    return doc


class TestIndexAndSearch:
    def test_migration_records_version(self, fts_db):
        rows = fts_db.execute(text("SELECT version FROM schema_migrations")).fetchall()
        assert 8 in {r[0] for r in rows}

    def test_fts_table_really_exists(self, fts_db):
        """确认真走了 FTS5 索引，而不是静默降级为 instr 扫描。"""
        row = fts_db.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'document_fts'")
        ).fetchone()
        assert row is not None, "FTS5 虚表未创建（被降级了）"
        assert "fts5" in row[0].lower()

    def test_search_three_chars(self, fts_db):
        """3 字中文（结算单）走 trigram 命中。"""
        doc = _add_doc(fts_db, "2026年6月结算单\n山西宏达钢铁有限公司\n金额 7,922,962.05")
        # 先证明 FTS 索引本身可查（而非静默走 instr 降级）
        direct = fts_db.execute(
            text('SELECT rowid FROM document_fts WHERE document_fts MATCH :q'),
            {"q": '"结算单"'},
        ).fetchall()
        assert {int(r[0]) for r in direct} == {doc.id}
        assert doc.id in fulltext_doc_ids(fts_db, "结算单")
        assert doc.id in fulltext_doc_ids(fts_db, "宏达钢铁")

    def test_search_short_keyword_fallback(self, fts_db):
        """2 字词（发票）低于 trigram 下限，走 instr 扫描仍能命中。"""
        doc = _add_doc(fts_db, "增值税专用发票 号码 01234567")
        assert doc.id in fulltext_doc_ids(fts_db, "发票")
        assert doc.id in fulltext_doc_ids(fts_db, "号码")

    def test_no_hit_returns_empty(self, fts_db):
        _add_doc(fts_db, "普通说明文档，无相关字样")
        assert fulltext_doc_ids(fts_db, "不存在的词") == set()

    def test_blank_keyword_returns_empty(self, fts_db):
        _add_doc(fts_db, "任意内容")
        assert fulltext_doc_ids(fts_db, "") == set()
        assert fulltext_doc_ids(fts_db, "   ") == set()


class TestTriggerSync:
    def test_update_refreshes_index(self, fts_db):
        """改正文后：旧词搜不到、新词能搜到（验证 UPDATE 触发器）。"""
        doc = _add_doc(fts_db, "原始内容：运输合同")
        assert doc.id in fulltext_doc_ids(fts_db, "运输合同")

        doc.extracted_text = "更正内容：采购合同"
        fts_db.commit()

        assert doc.id not in fulltext_doc_ids(fts_db, "运输合同")
        assert doc.id in fulltext_doc_ids(fts_db, "采购合同")

    def test_insert_after_index_built(self, fts_db):
        """索引建好之后新入库的文档也能搜到（验证 INSERT 触发器）。"""
        doc = _add_doc(fts_db, "新建档案：磅单 毛重 12.5 吨", name="磅单.pdf")
        assert doc.id in fulltext_doc_ids(fts_db, "磅单")


class TestSnippet:
    def test_snippet_marks_keyword(self, fts_db):
        doc = _add_doc(fts_db, "结算单明细如下，合计金额 7,922,962.05 元，已核验。")
        snip = fulltext_snippets(fts_db, "结算单", [doc.id])[doc.id]
        assert "<mark>结算单</mark>" in snip

    def test_snippet_escapes_html(self, fts_db):
        """正文里的 HTML 字符必须转义，避免前端 v-html 注入。"""
        doc = _add_doc(fts_db, "<script>alert(1)</script> 结算单")
        snip = fulltext_snippets(fts_db, "结算单", [doc.id])[doc.id]
        assert "&lt;script&gt;" in snip
        assert "<script>" not in snip

    def test_snippet_empty_when_no_hit(self, fts_db):
        doc = _add_doc(fts_db, "无关内容")
        assert fulltext_snippets(fts_db, "结算单", [doc.id]) == {}

    def test_snippet_handles_empty_ids(self, fts_db):
        assert fulltext_snippets(fts_db, "结算单", []) == {}


class TestApiSearch:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from backend.config import settings
        from backend.main import app

        monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
        monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
        monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
        (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
        with TestClient(app) as c:
            yield c

    def test_keyword_hits_body_and_returns_snippet(self, client):
        """文件名/字段都不含关键词，但正文含 → 仍应搜到并带片段。"""
        with SessionLocal() as db:
            doc = _add_doc(
                db,
                "结算单：本单货款合计 7,922,962.05 元，付款方山西宏达钢铁有限公司。",
                name="scan_001.pdf",
            )
            doc_id = doc.id

        r = client.get("/api/documents", params={"keyword": "宏达钢铁"})
        assert r.status_code == 200
        items = r.json()
        hit = next((i for i in items if i["id"] == doc_id), None)
        assert hit is not None, "正文命中却未返回该文档"
        assert hit["text_snippet"]
        assert "<mark>宏达钢铁</mark>" in hit["text_snippet"]

    def test_keyword_without_body_hit_still_searches_filename(self, client):
        """正文检索不影响原有的文件名搜索。"""
        with SessionLocal() as db:
            doc = _add_doc(db, "正文完全不含关键词", name="发票_202606.pdf")
            doc_id = doc.id

        r = client.get("/api/documents", params={"keyword": "发票"})
        assert r.status_code == 200
        assert any(i["id"] == doc_id for i in r.json())


class TestStartupPath:
    """真实启动链路：init_db() 必须建好索引并回灌历史正文。

    回归点：新建库走 fresh baseline 会跳过全部迁移，create_all 也不建虚表，
    所以索引只能靠 init_db 里的 ensure_fulltext_index() 兜住。
    """

    def test_init_db_creates_index_and_backfills(self):
        from backend.database import init_db

        with SessionLocal() as db:
            doc_id = _add_doc(db, "历史正文：结算单 2026 年 6 月 货款 7,922,962.05 元").id

        init_db()

        with SessionLocal() as db:
            ddl = db.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'document_fts'")
            ).fetchone()
            assert ddl is not None, "init_db 未创建 FTS5 虚表"
            assert "fts5" in ddl[0].lower()
            # 索引建好前就存在的文档，必须被回灌
            assert doc_id in fulltext_doc_ids(db, "结算单")

    def test_init_db_is_idempotent(self):
        """重复启动不报错、不重复回灌。"""
        from backend.database import init_db

        init_db()
        with SessionLocal() as db:
            before = db.execute(text("SELECT count(*) FROM document_fts")).scalar()
        init_db()
        with SessionLocal() as db:
            after = db.execute(text("SELECT count(*) FROM document_fts")).scalar()
        assert before == after
