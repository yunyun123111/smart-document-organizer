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
os.environ["AI_BASE_URL"] = ""
os.environ["AI_API_KEY"] = ""
os.environ["ALLOW_OVERWRITE"] = "False"
os.environ["ACCESS_PASSWORD"] = ""  # 测试环境关闭访问密码鉴权，避免 .env 密码导致全部 API 401

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
    Base.metadata.drop_all(bind=engine)
    # schema_migrations 不在 ORM metadata 中，需手动清理，避免跨用例残留
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))


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
