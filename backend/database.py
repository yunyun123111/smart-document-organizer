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
    # 新建库走 fresh baseline 会跳过迁移、create_all 也不建虚表，此处无条件确保索引存在
    with SessionLocal() as db:
        if ensure_fulltext_index(db):
            logger.info("全文索引 document_fts 就绪")
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
# 公司简称 + 紧凑日期在前，便于按客户/时间排序；结算与货权转移多保留船名
DEFAULT_RENAME_TEMPLATES: dict[str, str] = {
    "合同/结算单": "{公司:short}_{日期:compact}_{船名}_{编号}",
    "合同/货权转移": "{公司:short}_{日期:compact}_{船名}_{编号}",
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
SCHEMA_VERSION = 10
# 无迁移记录的老库按此版本视为"历史结构已齐"（v2~v4 的建索引语句依赖旧表列，
# 不在最老的库上重跑），只补 v5 及之后新增的迁移
_LEGACY_BASE_VERSION = 4

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


def _migrate_create_excel_sources(db: Session) -> None:
    """V1.0-登记表: 新增 excel_sources / excel_sheet_configs 表（Excel 权威数据源匹配）。幂等。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS excel_sources (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(255) NOT NULL DEFAULT '', file_path VARCHAR(1024) NOT NULL DEFAULT '', file_hash VARCHAR(64) NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT 1, total_sheets INTEGER NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    ))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_excel_sources_file_hash ON excel_sources (file_hash)"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS excel_sheet_configs (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL, sheet_name VARCHAR(255) NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT 1, doc_type_hint VARCHAR(100) NOT NULL DEFAULT '', column_map TEXT NOT NULL DEFAULT '{}', key_column VARCHAR(255) NOT NULL DEFAULT '', row_count INTEGER NOT NULL DEFAULT 0, last_error VARCHAR(500) NOT NULL DEFAULT '', created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    ))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_excel_sheet_configs_source_id ON excel_sheet_configs (source_id)"))
    db.commit()


def _migrate_create_business_archive(db: Session) -> None:
    """V2.0-01: 新增 business_records / business_files 表（业务档案模块）。幂等。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS business_records ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_no VARCHAR(255) NOT NULL, "
        "title VARCHAR(512) NOT NULL DEFAULT '', "
        "business_type VARCHAR(100) NOT NULL DEFAULT '', "
        "status VARCHAR(20) NOT NULL DEFAULT 'active', "
        "ship_name VARCHAR(255) NOT NULL DEFAULT '', "
        "counterparty VARCHAR(255) NOT NULL DEFAULT '', "
        "total_amount NUMERIC(18,2) NOT NULL DEFAULT 0, "
        "sign_date DATE, "
        "extra_data TEXT NOT NULL DEFAULT '{}', "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ))
    db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_records_business_no "
        "ON business_records (business_no)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_business_records_status "
        "ON business_records (status)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_business_records_ship_name "
        "ON business_records (ship_name)"
    ))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS business_files ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "document_id INTEGER NOT NULL, "
        "file_role VARCHAR(20) NOT NULL DEFAULT 'other', "
        "is_primary BOOLEAN NOT NULL DEFAULT 0, "
        "link_source VARCHAR(20) NOT NULL DEFAULT 'manual', "
        "sort_order INTEGER NOT NULL DEFAULT 0, "
        "created_at DATETIME NOT NULL, "
        "FOREIGN KEY (business_id) REFERENCES business_records (id) ON DELETE CASCADE, "
        "FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE)"
    ))
    db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_files_business_document "
        "ON business_files (business_id, document_id)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_business_files_document_id "
        "ON business_files (document_id)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_business_files_business_id "
        "ON business_files (business_id)"
    ))
    # 记录迁移版本（自包含：确保记录表存在；INSERT OR REPLACE 幂等，
    # run_migrations 随后也会统一记录，双写无害）
    _ensure_schema_migrations(db)
    _record_migration(db, 4, "create business_archive")
    db.commit()


def _migrate_create_fingerprints_and_anomalies(db: Session) -> None:
    """V5: 新增 document_fingerprints（文档指纹）+ dossier_anomalies（业务异常）表。幂等。

    文档指纹表：版面布局哈希 + 结构关键词 SimHash，支撑同类文件自动聚类免审；
    业务异常表：V2.5 异常检测的落库表（缺失单据/金额不符/日期冲突/数量偏差）。
    """
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS document_fingerprints ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "document_id INTEGER NOT NULL UNIQUE, "
        "layout_hash VARCHAR(64) NOT NULL, "
        "text_simhash VARCHAR(64) NOT NULL, "
        "feature_meta TEXT NOT NULL DEFAULT '{}', "
        "matched_template_id INTEGER, "
        "created_at DATETIME NOT NULL, "
        "FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE, "
        "FOREIGN KEY (matched_template_id) REFERENCES recognition_templates (id) ON DELETE SET NULL)"
    ))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fingerprints_layout ON document_fingerprints (layout_hash)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fingerprints_simhash ON document_fingerprints (text_simhash)"))
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS dossier_anomalies ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_id INTEGER NOT NULL, "
        "anomaly_type VARCHAR(32) NOT NULL, "
        "severity VARCHAR(16) NOT NULL DEFAULT 'WARNING', "
        "title VARCHAR(128) NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', "
        "related_document_ids TEXT NOT NULL DEFAULT '[]', "
        "is_resolved BOOLEAN NOT NULL DEFAULT 0, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "FOREIGN KEY (business_id) REFERENCES business_records (id) ON DELETE CASCADE)"
    ))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_business_id ON dossier_anomalies (business_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_resolved ON dossier_anomalies (is_resolved)"))
    _ensure_schema_migrations(db)
    _record_migration(db, 5, "create fingerprints and anomalies")
    db.commit()


def _migrate_create_reconciliation(db: Session) -> None:
    """V6-01: 新增 reconciliation_reports 表（业务线台账自动对账结果）。幂等。

    一条业务线一行，差异明细存 report_json；不改 business_records 主键。
    """
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS reconciliation_reports ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "business_no VARCHAR(255) NOT NULL, "
        "ledger_sheet VARCHAR(64) NOT NULL DEFAULT '', "
        "up_contract_no VARCHAR(255) NOT NULL DEFAULT '', "
        "down_contract_no VARCHAR(255) NOT NULL DEFAULT '', "
        "up_settlement NUMERIC(18,2) NOT NULL DEFAULT 0, "
        "down_settlement NUMERIC(18,2) NOT NULL DEFAULT 0, "
        "interest_receivable NUMERIC(18,2) NOT NULL DEFAULT 0, "
        "interest_received NUMERIC(18,2) NOT NULL DEFAULT 0, "
        "status VARCHAR(16) NOT NULL DEFAULT 'ok', "
        "diff_count INTEGER NOT NULL DEFAULT 0, "
        "report_json TEXT NOT NULL DEFAULT '{}', "
        "checked_at DATETIME NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ))
    db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_reconciliation_business_no "
        "ON reconciliation_reports (business_no)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_reconciliation_reports_status "
        "ON reconciliation_reports (status)"
    ))
    _record_migration(db, 6, "create reconciliation reports")
    db.commit()


def _migrate_create_external_details(db: Session) -> None:
    """V7-01: 新增 external_details 表（外部明细表导入，参考口径，不参与判定）。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS external_details ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "batch_no VARCHAR(64) NOT NULL DEFAULT '', "
        "business_no VARCHAR(255) NOT NULL DEFAULT '', "
        "occurred_on DATE, "
        "amount NUMERIC(18,2) NOT NULL DEFAULT 0, "
        "kind VARCHAR(16) NOT NULL DEFAULT 'receipt', "
        "note VARCHAR(500) NOT NULL DEFAULT '', "
        "source_filename VARCHAR(255) NOT NULL DEFAULT '', "
        "imported_at DATETIME NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_external_details_batch ON external_details (batch_no)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_external_details_business ON external_details (business_no)"
    ))
    _record_migration(db, 7, "create external details")
    db.commit()


def _migrate_create_review_decisions(db: Session) -> None:
    """V9-01: 新增 review_decisions 表（人工审核决策留痕）。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS review_decisions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "document_id INTEGER, "
        "action VARCHAR(20) NOT NULL DEFAULT 'approve', "
        "source VARCHAR(20) NOT NULL DEFAULT 'single', "
        "suggested_category VARCHAR(500), "
        "suggested_document_type VARCHAR(200), "
        "confidence FLOAT, "
        "final_category VARCHAR(500), "
        "final_document_type VARCHAR(200), "
        "category_changed BOOLEAN NOT NULL DEFAULT 0, "
        "type_changed BOOLEAN NOT NULL DEFAULT 0, "
        "sample_id INTEGER, "
        "rule_candidate_id INTEGER, "
        "note TEXT NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_review_decisions_document_id ON review_decisions (document_id)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_review_decisions_created_at ON review_decisions (created_at)"
    ))
    _record_migration(db, 9, "create review decisions")
    db.commit()


def _migrate_create_rule_candidates(db: Session) -> None:
    """V10-01: 新增 rule_candidates 表（人工改判沉淀的候选规则）。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS rule_candidates ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "kind VARCHAR(30) NOT NULL DEFAULT 'filename_prefix', "
        "keyword VARCHAR(200) NOT NULL, "
        "category_path VARCHAR(500) NOT NULL, "
        "document_type VARCHAR(200), "
        "from_doc_id INTEGER, "
        "from_decision_id INTEGER, "
        "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
        "adopted_rule_id INTEGER, "
        "hit_total INTEGER NOT NULL DEFAULT 0, "
        "hit_target INTEGER NOT NULL DEFAULT 0, "
        "note TEXT NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rule_candidates_keyword ON rule_candidates (keyword)"
    ))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rule_candidates_status ON rule_candidates (status)"
    ))
    _record_migration(db, 10, "create rule candidates")
    db.commit()


def ensure_fulltext_index(db: Session) -> bool:
    """幂等创建正文全文索引（documents.extracted_text 的 FTS5 trigram 虚表）。

    选 trigram 分词器：内置 unicode61 会把整段连续汉字当成一个词，中文搜不到；
    trigram 按 3 字符滑窗建索引，中文子串可直接命中。
    用 external content 模式（content='documents'）不重复存正文；
    写入由触发器同步，任何代码路径都不会漏索引。

    与迁移分离的原因：新建库走 fresh baseline 会跳过所有迁移，而 create_all 不建虚表，
    所以启动时必须无条件确保索引存在。仅在索引为空时回灌，避免每次启动全量 rebuild。
    返回索引是否可用（False 表示已降级为 instr 扫描）。
    """
    _ensure_schema_migrations(db)
    try:
        # 判断虚表是否刚刚创建：只有首次建表才需要回灌（external content 模式下
        # count(*) FROM document_fts 会回落到 content 表，不能用它判断索引是否为空）
        existed = db.execute(text(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='document_fts'"
        )).scalar()
        db.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5("
            "extracted_text, content='documents', content_rowid='id', tokenize='trigram')"
        ))
        db.execute(text(
            "CREATE TRIGGER IF NOT EXISTS documents_fts_ai AFTER INSERT ON documents BEGIN "
            "INSERT INTO document_fts(rowid, extracted_text) "
            "VALUES (new.id, coalesce(new.extracted_text, '')); "
            "END"
        ))
        db.execute(text(
            "CREATE TRIGGER IF NOT EXISTS documents_fts_ad AFTER DELETE ON documents BEGIN "
            "INSERT INTO document_fts(document_fts, rowid, extracted_text) "
            "VALUES ('delete', old.id, coalesce(old.extracted_text, '')); "
            "END"
        ))
        db.execute(text(
            "CREATE TRIGGER IF NOT EXISTS documents_fts_au AFTER UPDATE OF extracted_text ON documents BEGIN "
            "INSERT INTO document_fts(document_fts, rowid, extracted_text) "
            "VALUES ('delete', old.id, coalesce(old.extracted_text, '')); "
            "INSERT INTO document_fts(rowid, extracted_text) "
            "VALUES (new.id, coalesce(new.extracted_text, '')); "
            "END"
        ))
        # 首次建表时把已有正文一次性灌入；已存在的索引不再重灌，避免每次启动全量重建
        if not existed:
            db.execute(text("INSERT INTO document_fts(document_fts) VALUES('rebuild')"))
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        # 老版本 SQLite 不支持 trigram 时降级：搜索层会回退 instr 扫描
        db.rollback()
        logger.warning("FTS5 trigram 索引创建失败，全文检索降级为 instr 扫描")
        return False


def _migrate_create_fulltext_index(db: Session) -> None:
    """V8-01: 正文全文索引迁移（建表逻辑见 ensure_fulltext_index）。"""
    if not ensure_fulltext_index(db):
        return
    # 索引可用后再记迁移（避免记录成功但 DDL 失败的不一致）
    _record_migration(db, 8, "create documents fulltext index")
    db.commit()


MIGRATIONS: list[tuple[int, str, Callable[[Session], None]]] = [
    (2, "create recycle_bin", _migrate_create_recycle_bin),
    (3, "create excel_sources", _migrate_create_excel_sources),
    (4, "create business_archive", _migrate_create_business_archive),
    (5, "create fingerprints and anomalies", _migrate_create_fingerprints_and_anomalies),
    (6, "create reconciliation reports", _migrate_create_reconciliation),
    (7, "create external details", _migrate_create_external_details),
    (8, "create documents fulltext index", _migrate_create_fulltext_index),
    (9, "create review decisions", _migrate_create_review_decisions),
    (10, "create rule candidates", _migrate_create_rule_candidates),
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
    - 新库（无业务表）：直接标记 fresh baseline = SCHEMA_VERSION，跳过全部迁移
      （create_all 已建出最新结构）
    - 老库（有业务表但无迁移记录）：视为 v4 结构（v2~v4 迁移在更早的库上都已应用过，
      且它们的建索引语句依赖已存在的列），补记 v2~v4 后执行 v5 及之后的迁移
    - 之后每次升级：SCHEMA_VERSION +1 + MIGRATIONS 追加，老库执行新迁移，新库跳过
    """
    _ensure_schema_migrations(db)
    applied = _applied_versions(db)
    if not applied:
        if _has_business_tables(db):
            base = _LEGACY_BASE_VERSION
            _record_migration(db, 0, "legacy baseline")
            logger.info("数据库迁移：检测到既有库，标记 legacy baseline=v%d，补齐后续迁移", base)
            # 持久化记录已应用的历史迁移，避免下次因记录缺失而重复执行
            for v, name, _ in MIGRATIONS:
                if v <= base:
                    _record_migration(db, v, name)
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
