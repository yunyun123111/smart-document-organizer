"""数据库迁移机制测试（P0-2）。

覆盖：
- 新库：标记 fresh baseline，跳过全部迁移
- 老库（有业务表、无迁移记录）：标记 legacy baseline
- 未来新增迁移：老库执行、新库跳过、重复执行幂等
- 迁移函数必须幂等（重复跑不报错）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import backend.database as database


def _make_session(tmp_path, name: str = "t.db"):
    eng = create_engine(
        f"sqlite:///{tmp_path}/{name}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=eng)
    return eng, Session


def _table_names(db) -> set[str]:
    rows = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    ).fetchall()
    return {r[0] for r in rows}


def _migration_names(db) -> list[tuple[int, str]]:
    rows = db.execute(text("SELECT version, name FROM schema_migrations ORDER BY version")).fetchall()
    return [(r[0], r[1]) for r in rows]


class TestBaseline:
    def test_fresh_db_records_fresh_baseline(self, tmp_path):
        """全新空库：标记 fresh baseline，不产生业务表，无迁移执行。"""
        eng, Session = _make_session(tmp_path)
        with Session() as db:
            database.run_migrations(db)
            recs = _migration_names(db)
            assert (0, "fresh baseline") in recs
            assert _table_names(db) == {"schema_migrations"}
        eng.dispose()

    def test_legacy_db_records_legacy_baseline(self, tmp_path):
        """老库（有业务表、无迁移记录）：标记 legacy baseline。"""
        eng, Session = _make_session(tmp_path)
        with Session() as db:
            db.execute(text("CREATE TABLE documents (id INTEGER PRIMARY KEY, name TEXT)"))
            db.commit()
            database.run_migrations(db)
            recs = _migration_names(db)
            assert (0, "legacy baseline") in recs
        eng.dispose()

    def test_rerun_is_noop(self, tmp_path):
        """已迁移的库重复运行：不改变记录、不报错。"""
        eng, Session = _make_session(tmp_path)
        with Session() as db:
            database.run_migrations(db)
            before = _migration_names(db)
            database.run_migrations(db)
            assert _migration_names(db) == before
        eng.dispose()


class TestFutureMigration:
    def _add_col_fn(self, colname: str):
        """构造一个幂等的加列迁移（列存在则跳过）。"""

        def _upgrade(db):
            rows = db.execute(
                text("PRAGMA table_info(documents)")
            ).fetchall()
            has = any(r[1] == colname for r in rows)
            if not has:
                db.execute(text(f"ALTER TABLE documents ADD COLUMN {colname} TEXT"))
                db.commit()

        return _upgrade

    def test_new_migration_applied_on_legacy(self, tmp_path, monkeypatch):
        """老库升级：新增更高版本迁移（模拟 v3 发布）会被执行，且只执行一次。"""
        eng, Session = _make_session(tmp_path)
        # 模拟老库：业务表 + legacy baseline（当前最新版本之前的库）
        with Session() as db:
            db.execute(text("CREATE TABLE documents (id INTEGER PRIMARY KEY, name TEXT)"))
            db.commit()
            database.run_migrations(db)  # 打 legacy baseline + 应用当前已有迁移

        # 模拟发布新版本：SCHEMA_VERSION=3，新增 v3 迁移
        monkeypatch.setattr(database, "SCHEMA_VERSION", 3)
        monkeypatch.setattr(
            database, "MIGRATIONS",
            [(3, "add demo col", self._add_col_fn("demo_col"))],
        )
        with Session() as db:
            database.run_migrations(db)
            assert (3, "add demo col") in _migration_names(db)
            cols = {r[1] for r in db.execute(text("PRAGMA table_info(documents)")).fetchall()}
            assert "demo_col" in cols

        # 再次运行 → 幂等，不重复插入迁移记录
        with Session() as db:
            database.run_migrations(db)
            assert _migration_names(db).count((3, "add demo col")) == 1
        eng.dispose()

    def test_new_migration_skipped_on_fresh(self, tmp_path, monkeypatch):
        """新库：create_all 已是最新结构，迁移直接标记已应用、不执行。"""
        monkeypatch.setattr(database, "SCHEMA_VERSION", 3)
        monkeypatch.setattr(
            database, "MIGRATIONS",
            [(3, "add demo col", self._add_col_fn("demo_col"))],
        )
        eng, Session = _make_session(tmp_path)
        with Session() as db:
            database.run_migrations(db)
            # 新库 fresh baseline=SCHEMA_VERSION=3 → v3 被逻辑标记为已应用，不执行
            assert (0, "fresh baseline") in _migration_names(db)
            assert (3, "add demo col") not in _migration_names(db)
            # 未执行迁移函数：documents 业务表不存在（应由 create_all 负责建最新结构）
            assert "documents" not in _table_names(db)
        eng.dispose()

    def test_migration_idempotent_direct_call(self, tmp_path):
        """迁移函数本身幂等：对已含目标列的库重复执行不报错。"""
        eng, Session = _make_session(tmp_path)
        fn = self._add_col_fn("demo_col")
        with Session() as db:
            db.execute(text("CREATE TABLE documents (id INTEGER PRIMARY KEY, name TEXT)"))
            db.execute(text("ALTER TABLE documents ADD COLUMN demo_col TEXT"))
            db.commit()
            fn(db)  # 列已存在 → 跳过，不抛错
            fn(db)
        eng.dispose()
