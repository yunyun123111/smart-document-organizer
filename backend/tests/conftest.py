"""pytest 公共配置。

关键点：必须在 import 任何 backend 模块之前，把 DATABASE_URL 指向独立的临时测试库，
避免污染开发数据库 data/database/app.db。
"""
from __future__ import annotations

import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="sdo_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
os.environ["LOG_LEVEL"] = "ERROR"
# ---- 测试隔离：屏蔽开发者本机 .env 的外部副作用 ----
# pydantic-settings 中环境变量优先于 .env。若不显式覆盖：
# 1) 本机 .env 里的 AI_BASE_URL/AI_API_KEY 会让识别链路真的向云端发起请求
#    （测试结果不稳定、慢，且把文档内容发给外部服务）；
# 2) 本机 ALLOW_OVERWRITE=True 会让"重名禁止覆盖"的安全断言在错误配置下运行。
os.environ["AI_ENABLED"] = "False"
# 视觉 KIE 兜底默认关闭：测试不应向本机 Ollama 发真实请求（单页约 50s）
os.environ["OCR_KIE_ENABLED"] = "False"
os.environ["OCR_PROVIDER"] = "rapidocr"
os.environ["AI_BASE_URL"] = ""
os.environ["AI_API_KEY"] = ""
os.environ["ALLOW_OVERWRITE"] = "False"
os.environ["ACCESS_PASSWORD"] = ""  # 测试环境关闭访问密码鉴权，避免 .env 密码导致全部 API 401
os.environ["ARCHIVE_TEMPLATE"] = "category/year/month"  # 测试基线：默认归档模板，不受本机 .env 影响

from sqlalchemy import text

import pytest

from backend.config import settings
from backend.database import (
    Base,
    SessionLocal,
    engine,
    seed_default_categories,
    seed_default_rules,
)
from backend.utils.logger import get_operation_logger, get_logger

# 测试中静默日志
get_logger("sdo").disabled = True


@pytest.fixture(autouse=True)
def _isolated_db():
    """每个测试用例独立的空库：用例前建表，用例后清空所有表。

    注意：只建表、不写默认数据，需要默认分类的测试显式调用 seed_default_categories。
    """
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    yield
    # FTS5 虚表与触发器不在 ORM metadata 中，必须手动删掉，
    # 否则残留索引会跨用例引用已删除的 documents 表
    _drop_fulltext_objects()
    Base.metadata.drop_all(bind=engine)
    # schema_migrations 不在 ORM metadata 中，需手动清理，避免跨用例残留
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))


def _drop_fulltext_objects() -> None:
    """清理全文索引对象（触发器先删，避免引用已删表）。"""
    with engine.begin() as conn:
        for trg in ("documents_fts_ai", "documents_fts_ad", "documents_fts_au"):
            conn.execute(text(f"DROP TRIGGER IF EXISTS {trg}"))
        conn.execute(text("DROP TABLE IF EXISTS document_fts"))


@pytest.fixture
def db():
    """提供一个数据库会话，用完自动关闭。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seeded_db(db):
    """已写入默认分类 + 默认规则的会话。"""
    seed_default_categories(db)
    seed_default_rules(db)
    return db
