"""数据库连接、会话与初始化。

- 统一创建 SQLAlchemy engine / Session
- init_db()：建表 + 首次启动写入默认分类（规格书第五十节）
"""
from __future__ import annotations

from typing import Callable, Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from datetime import datetime

from backend.config import settings
from backend.models import Base, Category, FilenameRule, RenameTemplate, Rule
from backend.utils.logger import get_logger

logger = get_logger("database")

_connect_args: dict = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite 多线程访问需要关闭同线程检查
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    echo=False,
    pool_pre_ping=True,
)

if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        """启用 SQLite WAL（支持并发读写）+ 外键约束 + 写锁等待。"""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=8000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个独立会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ 默认数据 ============
# 规格书第五十节：默认分类树（name -> 子分类列表）
DEFAULT_CATEGORY_TREE: dict[str, list[str]] = {
    "合同": ["采购合同", "销售合同", "框架合同", "补充协议", "结算单", "货权转移"],
    "财务": ["发票", "银行回单", "对账单", "收付款凭证"],
    "业务": ["报价单", "订单", "出货单", "入库单"],
    "项目资料": [],
    "证照": [],
    "其他": [],
}


def seed_default_categories(db: Session) -> int:
    """首次启动时写入默认分类树，返回创建的分类数（已存在则跳过）。"""
    created = 0
    for parent_name, children in DEFAULT_CATEGORY_TREE.items():
        existing_parent = (
            db.query(Category)
            .filter(Category.parent_id.is_(None), Category.name == parent_name)
            .first()
        )
        if existing_parent is None:
            parent = Category(name=parent_name, path=parent_name, sort_order=created)
            db.add(parent)
            db.flush()  # 拿到 parent.id
            created += 1
            for i, child_name in enumerate(children):
                child = Category(
                    name=child_name,
                    parent_id=parent.id,
                    path=f"{parent_name}/{child_name}",
                    sort_order=i,
                )
                db.add(child)
                created += 1
        else:
            # 父分类已存在，补齐缺失的子分类
            existing_children = {
                c.name for c in existing_parent.children if c.parent_id is not None
            }
            for i, child_name in enumerate(children):
                if child_name not in existing_children:
                    db.add(
                        Category(
                            name=child_name,
                            parent_id=existing_parent.id,
                            path=f"{parent_name}/{child_name}",
                            sort_order=i,
                        )
                    )
                    created += 1
    if created:
        db.commit()
        logger.info("默认分类初始化完成，新增 %d 个分类", created)
    return created


# 规格书第五十二节：默认识别关键词（分类路径 -> 关键词列表）
DEFAULT_RULES: dict[str, list[str]] = {
    "合同/销售合同": ["销售合同", "购销合同", "销售方", "买方", "卖方", "合同编号", "合同金额"],
    "合同/采购合同": ["采购合同", "采购方", "供应商", "供货方", "采购编号", "采购金额"],
    "财务/发票": ["增值税专用发票", "增值税普通发票", "发票号码", "开票日期", "税率", "税额"],
    "财务/银行回单": ["银行电子回单", "付款人", "收款人", "交易金额", "交易日期", "回单"],
    "财务/对账单": ["对账单", "对账", "账期"],
    "财务/收付款凭证": ["收款凭证", "付款凭证", "收据", "进账单"],
    "业务/报价单": ["报价单", "报价", "询价"],
    "业务/订单": ["订单", "订单号", "订货", "订购"],
    "业务/出货单": ["出货单", "发货单", "出库单", "送货单"],
    "业务/入库单": ["入库单", "收货单", "入库"],
    "合同/结算单": ["结算单", "结算", "结算确认", "结算明细"],
    "合同/货权转移": ["货权转移", "货权", "货转", "货权转移确认单"],
}


def seed_default_rules(db: Session) -> int:
    """写入默认关键词规则（幂等）。返回新增规则数。"""
    created = 0
    for path, keywords in DEFAULT_RULES.items():
        category = (
            db.query(Category)
            .filter(Category.path == path, Category.enabled.is_(True))
            .first()
        )
        if category is None:
            logger.warning("默认规则目标分类不存在，跳过: %s", path)
            continue
        existing = {r.keyword for r in category.rules if r.enabled}
        for kw in keywords:
            if kw not in existing:
                db.add(
                    Rule(
                        category_id=category.id,
                        keyword=kw,
                        match_type="contains",
                        priority=10,
                        weight=1.0,
                        enabled=True,
                    )
                )
                created += 1
    if created:
        db.commit()
        logger.info("默认规则初始化完成，新增 %d 条规则", created)
    return created


def init_db() -> None:
    """初始化数据库：确保目录、建表、写入默认数据、应用迁移。"""
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        run_migrations(db)
    logger.info("数据库表结构已就绪: %s", settings.DATABASE_URL)
    with SessionLocal() as db:
        seed_default_categories(db)
        seed_default_rules(db)
        seed_default_filename_rules(db)
        seed_default_rename_templates(db)


# 文件名规则：业务编码前缀 -> 分类路径（命中直接归档，跳过内容识别）
DEFAULT_FILENAME_RULES: list[tuple[str, str, str]] = [
    ("SJWLXS", "合同/销售合同", "SJWLXS=销售合同"),
    ("HSYCXS", "合同/销售合同", "HSYCXS=销售合同"),
    ("SJWLCG", "合同/采购合同", "SJWLCG=采购合同"),
    ("HSYCCG", "合同/采购合同", "HSYCCG=采购合同"),
]


def seed_default_filename_rules(db: Session) -> int:
    """写入默认文件名规则（幂等）。返回新增规则数。"""
    created = 0
    for pattern, path, note in DEFAULT_FILENAME_RULES:
        category = (
            db.query(Category)
            .filter(Category.path == path, Category.enabled.is_(True))
            .first()
        )
        if category is None:
            logger.warning("文件名规则目标分类不存在，跳过: %s", path)
            continue
        existing = {
            r.pattern.lower()
            for r in db.query(FilenameRule).filter(FilenameRule.category_id == category.id)
        }
        if pattern.lower() not in existing:
            db.add(
                FilenameRule(
                    category_id=category.id,
                    pattern=pattern,
                    priority=100,
                    enabled=True,
                    note=note,
                )
            )
            created += 1
    if created:
        db.commit()
        logger.info("默认文件名规则初始化完成，新增 %d 条", created)
    return created

# 分类默认重命名模板（模糊文件识别后按此格式命名）
DEFAULT_RENAME_TEMPLATES: dict[str, str] = {
    "合同/结算单": "{日期}_{船名}_{合同号}_{物料}_{类型}",
    "合同/货权转移": "{日期}_{船名}_{合同号}_{物料}_{类型}",
}


def seed_default_rename_templates(db: Session) -> int:
    """写入分类默认重命名模板（幂等）。返回新增模板数。"""
    created = 0
    for path, template in DEFAULT_RENAME_TEMPLATES.items():
        category = (
            db.query(Category)
            .filter(Category.path == path, Category.enabled.is_(True))
            .first()
        )
        if category is None:
            logger.warning("重命名模板目标分类不存在，跳过: %s", path)
            continue
        existing = (
            db.query(RenameTemplate)
            .filter(RenameTemplate.category_id == category.id, RenameTemplate.enabled.is_(True))
            .first()
        )
        if existing is None:
            db.add(
                RenameTemplate(
                    category_id=category.id,
                    template=template,
                    enabled=True,
                )
            )
            created += 1
    if created:
        db.commit()
        logger.info("默认重命名模板初始化完成，新增 %d 条", created)
    return created


# ============ 数据库迁移机制（P0-2） ============
# 目标：改表结构一律走 MIGRATIONS，禁止手写 SQL 直接改库，
# 避免"数据库有列但代码 model 不知道"的漂移（如历史遗留的 document_scope）。
#
# 用法：需要改表结构时——
#   1. 在 ORM model 中加入新字段/新表（供 create_all / 新库使用）
#   2. SCHEMA_VERSION += 1
#   3. 在 MIGRATIONS 追加 (版本号, 描述, 迁移函数)；
#      迁移函数必须幂等（先检查列/表是否存在，存在则跳过）
_SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
SCHEMA_VERSION = 2

# 迁移列表：[(version, name, upgrade_fn)]
# upgrade_fn(db: Session) -> None，须幂等。
MIGRATIONS: list[tuple[int, str, Callable[[Session], None]]] = []


def _migrate_create_recycle_bin(db: Session) -> None:
    """V1.5-01: 新增 recycle_bin 表（回收站）。幂等。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS recycle_bin (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, original_filename VARCHAR(512) NOT NULL, current_filename VARCHAR(512) NOT NULL DEFAULT '', original_path VARCHAR(1024) NOT NULL DEFAULT '', current_path VARCHAR(1024) NOT NULL DEFAULT '', recycle_path VARCHAR(1024) NOT NULL DEFAULT '', file_hash VARCHAR(64) NOT NULL DEFAULT '', file_size INTEGER NOT NULL DEFAULT 0, file_type VARCHAR(20) NOT NULL DEFAULT '', document_type VARCHAR(200) NOT NULL DEFAULT '', category_path VARCHAR(500) NOT NULL DEFAULT '', original_status VARCHAR(20) NOT NULL DEFAULT '', deleted_reason VARCHAR(500) NOT NULL DEFAULT '', original_file_missing BOOLEAN NOT NULL DEFAULT 0, deleted_at DATETIME NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    ))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recycle_bin_document_id ON recycle_bin (document_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recycle_bin_deleted_at ON recycle_bin (deleted_at)"))
    db.commit()


MIGRATIONS: list[tuple[int, str, Callable[[Session], None]]] = [
    (2, "create recycle_bin", _migrate_create_recycle_bin),
]


def _ensure_schema_migrations(db: Session) -> None:
    db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_SCHEMA_MIGRATIONS_TABLE} ("
            "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        )
    )
    db.commit()


def _applied_versions(db: Session) -> set[int]:
    rows = db.execute(text(f"SELECT version FROM {_SCHEMA_MIGRATIONS_TABLE}")).fetchall()
    return {r[0] for r in rows}


def _record_migration(db: Session, version: int, name: str) -> None:
    db.execute(
        text(
            f"INSERT OR REPLACE INTO {_SCHEMA_MIGRATIONS_TABLE} "
            "(version, name, applied_at) VALUES (:v, :n, :t)"
        ),
        {"v": version, "n": name, "t": datetime.now().isoformat()},
    )
    db.commit()


def _has_business_tables(db: Session) -> bool:
    """判断是否已有业务表（老库）而非全新空库。"""
    rows = db.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != :m"
        ),
        {"m": _SCHEMA_MIGRATIONS_TABLE},
    ).fetchall()
    return bool(rows)


def run_migrations(db: Session) -> None:
    """按版本顺序应用未执行的迁移。

    - 新库（无业务表）：直接标记 fresh baseline = SCHEMA_VERSION，跳过全部迁移
      （create_all 已建出最新结构）
    - 老库（有业务表但无迁移记录）：标记 legacy baseline = SCHEMA_VERSION - 1，
      执行最后一个增量迁移补齐到最新
    - 之后每次升级：SCHEMA_VERSION +1 + MIGRATIONS 追加，老库执行新迁移，新库跳过
    """
    _ensure_schema_migrations(db)
    applied = _applied_versions(db)
    if not applied:
        if _has_business_tables(db):
            base = SCHEMA_VERSION - 1
            _record_migration(db, 0, "legacy baseline")
            logger.info("数据库迁移：检测到既有库，标记 legacy baseline=%d", base)
        else:
            base = SCHEMA_VERSION
            _record_migration(db, 0, "fresh baseline")
            logger.info("数据库迁移：新库 fresh baseline=%d", base)
        applied = {v for v, _, _ in MIGRATIONS if v <= base}
    else:
        # fresh baseline 库由 create_all 维护最新表结构，所有迁移视为已应用；
        # 否则重复运行 run_migrations 会把"逻辑跳过"的迁移误执行（幂等被破坏）
        row = db.execute(
            text(f"SELECT name FROM {_SCHEMA_MIGRATIONS_TABLE} WHERE version=0")
        ).fetchone()
        if row is not None and row[0] == "fresh baseline":
            applied = {v for v, _, _ in MIGRATIONS if v <= SCHEMA_VERSION}

    for v, name, fn in MIGRATIONS:
        if v <= SCHEMA_VERSION and v not in applied:
            logger.info("应用数据库迁移 v%d: %s", v, name)
            fn(db)
            _record_migration(db, v, name)
            applied.add(v)
