"""数据库连接、会话与初始化。

- 统一创建 SQLAlchemy engine / Session
- init_db()：建表 + 首次启动写入默认分类（规格书第五十节）
"""
from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
    """初始化数据库：确保目录、建表、写入默认数据。"""
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
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





