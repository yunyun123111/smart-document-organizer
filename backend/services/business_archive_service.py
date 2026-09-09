"""业务档案服务（V2.0）：文档 ↔ 业务档案自动/手动归集。

核心思路（业务背景）：
- 系统通过 OCR / 规则 / Excel 台账把关键字段提取到 document_fields
  （真实字段名：contract_no 合同号、vessel 船名、amount 金额、date 日期、
  company 公司、material 物料等）
- 业务档案以 business_no（合同号）为唯一键：一份合同号对应一条档案，
  该业务下的销售合同 / 结算单 / 发票 / 货权等文件全部挂到同一档案
- 结算单与销售合同共享 contract_no（如 SJWLXS（DD）-2026-YC0429），
  因此自动归集可把"结算单跟着合同走"落到档案维度

方法：
- auto_link_document：单文档自动归集（有合同号 → 查档案 → 无则建档案 → 挂关联）
- manual_link_file：人工把文档绑定到指定档案
- unlink_file：解除关联（不删除物理文档与 documents 记录）
- backfill_historical_data：扫描历史文档批量回填建档

安全与异常：
- 全程事务提交，单文档失败不中断批量回填
- 重复关联幂等（唯一约束兜底 + 先查后插）
- 无合同号的文档跳过建档（避免发票号等误当业务编号污染档案）
- 所有操作写入 operation_logs（自动/手动/解除三类操作类型）
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    BUSINESS_STATUS_ACTIVE,
    FILE_ROLE_CARGO_RIGHT,
    FILE_ROLE_CONTRACT,
    FILE_ROLE_INVOICE,
    FILE_ROLE_OTHER,
    FILE_ROLE_SETTLEMENT,
    LINK_SOURCE_AUTO_RULE,
    LINK_SOURCE_MANUAL,
    OP_BUSINESS_AUTO_LINK,
    OP_BUSINESS_MANUAL_LINK,
    OP_BUSINESS_UNLINK,
    RESULT_FAILED,
    RESULT_OK,
    BusinessFile,
    BusinessRecord,
    Document,
    DocumentField,
    STATUS_RECYCLED,
)
from backend.services.operation_service import log_operation
from backend.utils.logger import get_logger

logger = get_logger("services.business_archive_service")

# ---------------- 文档类型 → 文件角色映射 ----------------
# document_type 以现有数据实际取值为准（销售合同/采购合同/结算单/发票/货权…）
_DOCUMENT_TYPE_ROLE_MAP: dict[str, str] = {
    "销售合同": FILE_ROLE_CONTRACT,
    "采购合同": FILE_ROLE_CONTRACT,
    "合同": FILE_ROLE_CONTRACT,
    "结算单": FILE_ROLE_SETTLEMENT,
    "发票": FILE_ROLE_INVOICE,
    "货权转移单": FILE_ROLE_CARGO_RIGHT,
    "货权": FILE_ROLE_CARGO_RIGHT,
    "货权凭证": FILE_ROLE_CARGO_RIGHT,
    "货权转移凭证": FILE_ROLE_CARGO_RIGHT,
}

# ---------------- 合同号合法性 / 模糊匹配 / 档案完整度 ----------------
# 业务编号合法前缀（销售/采购 x 外矿/国内），发票号等非业务编号不得用于建档
_BUSINESS_NO_PREFIXES: tuple[str, ...] = ("SJWLXS", "SJWLCG", "HSYCXS", "HSYCCG")
# 合同号字段置信度低于该值不自动归集（手写/OCR 识别不清，留待人工确认）
_CONFIDENCE_THRESHOLD = 0.6
# 模糊匹配最低相似度（difflib），用于 OCR 手写后四位识别错时的候选兜底
_FUZZY_THRESHOLD = 0.75
# 前两名候选相似度差小于该值视为歧义，不自动归集
_FUZZY_AMBIGUITY_GAP = 0.08

# 业务档案期望单据角色（完整性检查）：合同 / 结算单 / 发票 / 货权
_EXPECTED_ROLES: tuple[str, ...] = (
    FILE_ROLE_CONTRACT,
    FILE_ROLE_SETTLEMENT,
    FILE_ROLE_INVOICE,
    FILE_ROLE_CARGO_RIGHT,
)
_ROLE_LABELS: dict[str, str] = {
    FILE_ROLE_CONTRACT: "合同",
    FILE_ROLE_SETTLEMENT: "结算单",
    FILE_ROLE_INVOICE: "发票",
    FILE_ROLE_CARGO_RIGHT: "货权",
    FILE_ROLE_OTHER: "其他",
}


def _normalize_no(no: str) -> str:
    """业务编号归一化：全角转半角、去空白下划线、连字符统一，便于比较。"""
    s = unicodedata.normalize("NFKC", (no or "").strip()).upper()
    s = re.sub(r"[\s_]+", "", s)
    return re.sub(r"-+", "-", s)


def _is_valid_business_no(no: str) -> bool:
    """校验是否为业务编号：必须匹配合法前缀且包含年份与数字编号。

    防止发票号（如 SDJZ-SXJG20260706）等非业务编号被误当成合同号建档。
    """
    norm = _normalize_no(no)
    if not norm:
        return False
    for prefix in _BUSINESS_NO_PREFIXES:
        if norm.startswith(prefix):
            rest = norm[len(prefix):]
            if "202" in rest and any(ch.isdigit() for ch in rest):
                return True
    return False

# 文件名合同号正则：SJWLXS（DD）-2026-YC0432 / SJWLCG（DD）-2026-0109 / HSYCXS（DD）-2026-0110
_FN_CONTRACT_NO_RE = re.compile(
    r"([A-Z]{2,10}(?:（DD）|\(DD\)|[-_])?[-_/]?\d{4}[-_/]?[A-Z]{0,6}\d{2,})|"
    r"([A-Z]{2,6}\d{4,})",
    re.IGNORECASE,
)


def _extract_no_from_filename(filename: str) -> str:
    """从文件名回退提取合同号（无则空串）。仅返回通过业务号校验的完整编号。"""
    if not filename:
        return ""
    m = _FN_CONTRACT_NO_RE.search(filename)
    if not m:
        return ""
    no = m.group(1) or m.group(2)
    return no if _is_valid_business_no(no) else ""


def _get_field_confidence(db: Session, document_id: int, field_name: str) -> float:
    """读取字段最新一条的置信度，无记录视为 1.0（不阻塞）。"""
    row = db.execute(
        select(DocumentField.confidence)
        .where(
            DocumentField.document_id == document_id,
            DocumentField.field_name == field_name,
        )
        .order_by(DocumentField.id.desc())
        .limit(1)
    ).first()
    if row and row[0] is not None:
        return float(row[0])
    return 1.0


def compute_completeness(present_roles: set[str]) -> dict:
    """档案完整性：期望单据角色集合与实际已有角色的差集与完整度百分比。"""
    present = set(present_roles or set())
    missing = [r for r in _EXPECTED_ROLES if r not in present]
    expected_count = len(_EXPECTED_ROLES)
    percent = (
        round((expected_count - len(missing)) / expected_count * 100)
        if expected_count
        else 0
    )
    return {
        "expected_roles": list(_EXPECTED_ROLES),
        "expected_labels": [_ROLE_LABELS.get(r, r) for r in _EXPECTED_ROLES],
        "present_roles": sorted(present),
        "missing_roles": missing,
        "missing_labels": [_ROLE_LABELS.get(r, r) for r in missing],
        "percent": percent,
        "complete": len(missing) == 0,
    }


# ---------------- document_fields 字段名 → 档案列映射 ----------------
# 真实字段名以数据库为准（vessel 船名 / amount 金额 / company 公司 / date 日期）
_BUSINESS_NO_FIELD = "contract_no"
_RECORD_FIELD_MAP: dict[str, str] = {
    "vessel": "ship_name",
    "amount": "total_amount",
    "company": "counterparty",
    "date": "sign_date",
}

# 金额 / 日期容错解析
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")


class BusinessArchiveError(Exception):
    """业务档案服务业务异常。"""


def _get_field(db: Session, document_id: int, field_name: str) -> str:
    """读取文档最新一条指定字段值（document_fields），无则返回空串。"""
    row = db.execute(
        select(DocumentField.field_value)
        .where(
            DocumentField.document_id == document_id,
            DocumentField.field_name == field_name,
        )
        .order_by(DocumentField.id.desc())
        .limit(1)
    ).first()
    return (row[0] if row else "").strip()


def _parse_amount(raw: str) -> Decimal:
    """金额解析：去除千分位/货币符号/空格，解析失败返回 0。"""
    if not raw:
        return Decimal("0")
    cleaned = raw.replace(",", "").replace("，", "").replace("¥", "").replace("￥", "").strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        logger.warning("金额字段解析失败，按 0 处理: %r", raw)
        return Decimal("0")


def _parse_date(raw: str) -> Optional[date]:
    """日期解析：支持常见分隔符，失败返回 None。"""
    if not raw:
        return None
    cleaned = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    logger.warning("日期字段解析失败，置空: %r", raw)
    return None


def _resolve_file_role(document_type: str) -> str:
    """按文档类型映射文件角色；未知类型归入 other。"""
    return _DOCUMENT_TYPE_ROLE_MAP.get(document_type or "", FILE_ROLE_OTHER)


class BusinessArchiveService:
    """业务档案服务。"""

    # ---------- 基础能力 ----------

    def get_business_by_no(self, db: Session, business_no: str) -> Optional[BusinessRecord]:
        """按业务编号（合同号）查档案。"""
        return db.execute(
            select(BusinessRecord).where(BusinessRecord.business_no == business_no)
        ).scalar_one_or_none()

    def get_business(self, db: Session, business_id: int) -> Optional[BusinessRecord]:
        """按 id 查档案。"""
        return db.get(BusinessRecord, business_id)

    def get_link(
        self, db: Session, business_id: int, document_id: int
    ) -> Optional[BusinessFile]:
        """查已有档案-文件关联。"""
        return db.execute(
            select(BusinessFile).where(
                BusinessFile.business_id == business_id,
                BusinessFile.document_id == document_id,
            )
        ).scalar_one_or_none()

    # ---------- 1. 自动归集 ----------

    def auto_link_document(self, db: Session, document_id: int) -> Optional[BusinessRecord]:
        """自动把单个文档归入业务档案。

        规则：
        - 读取文档的 contract_no（无合同号 → 跳过建档，返回 None）
        - 按合同号查 business_records：存在 → 复用；不存在 → 新建 active 档案
          （title/ship_name/counterparty/total_amount/sign_date 从字段自动填充）
        - 插入 business_files 关联（file_role 按文档类型映射，link_source=auto_rule）
        - 重复调用幂等：已关联则直接返回现有档案

        返回：文档所属的业务档案；无合同号/文档不存在/失败时返回 None。
        """
        document = db.get(Document, document_id)
        if document is None:
            logger.warning("自动归集跳过：文档不存在 document_id=%s", document_id)
            return None

        contract_no = _get_field(db, document_id, _BUSINESS_NO_FIELD)
        no_from_filename = False
        if not contract_no:
            # 文件名回退：SJWLXS（DD）-2026-YC0432.pdf 这类文件名规则直接归档的
            # 文档没有字段提取，从文件名提取合同号（文件名权威，置信度视为 1.0）
            contract_no = _extract_no_from_filename(document.original_filename)
            if contract_no:
                no_from_filename = True
                logger.info(
                    "自动归集：文档 %s 无字段合同号，从文件名提取 %s",
                    document_id, contract_no,
                )
        if not contract_no:
            logger.info(
                "自动归集跳过：文档 %s（%s）无合同号字段",
                document_id, document.original_filename,
            )
            return None

        # 1) 前缀合法性校验：发票号等非业务编号不得用于建档（防数据污染）
        if not _is_valid_business_no(contract_no):
            logger.info(
                "自动归集跳过：文档 %s 的合同号 %r 不是合法业务编号（疑似发票号），不建档",
                document_id, contract_no,
            )
            return None

        # 2) 低置信度：手写/OCR 识别不清 → 不自动归集，留待人工确认（文件名来源视为可信）
        conf = (
            1.0
            if no_from_filename
            else _get_field_confidence(db, document_id, _BUSINESS_NO_FIELD)
        )
        if conf < _CONFIDENCE_THRESHOLD:
            logger.info(
                "自动归集跳过：文档 %s 合同号 %r 置信度 %.2f 过低，待人工确认归集",
                document_id, contract_no, conf,
            )
            return None

        # 3) 匹配档案：索引精确 → 归一化精确 → 唯一高相似候选（模糊）→ 无匹配
        norm = _normalize_no(contract_no)
        business = None
        match_mode = "none"
        # 快路径：business_no 唯一索引直接命中（绝大多数场景，避免全表扫）
        business = self.get_business_by_no(db, contract_no)
        if business is not None:
            match_mode = "exact"
        else:
            # 慢路径：仅在索引未命中时全表归一化比对（容忍手写格式差异）。
            # ponytail: 全表扫只发生在未命中时；档案数千条后若慢，可加前缀过滤。
            for rec in db.execute(select(BusinessRecord)).scalars():
                if _normalize_no(rec.business_no) == norm:
                    business = rec
                    match_mode = "exact"
                    break
        if business is None and not no_from_filename:
            # 文件名来源的合同号权威可信（不存在 OCR 识别错），
            # 跳过模糊匹配暂停，直接精确匹配/新建，避免 YC 系列近号互相拦截
            fstate, candidate = self._find_fuzzy_business(db, contract_no)
            if fstate == "candidate":
                logger.info(
                    "自动归集暂停：文档 %s 合同号 %r 与既有档案 %r 高度相似，"
                    "待人工确认归集（避免误合并或误建档）",
                    document_id, contract_no, candidate.business_no,
                )
                return None
            if fstate == "ambiguous":
                logger.info(
                    "自动归集暂停：文档 %s 合同号 %r 与多个既有档案相似度过近，"
                    "待人工确认归集",
                    document_id, contract_no,
                )
                return None
            # fstate == "none"：无候选，走正常新建

        # 幂等：已有关联直接返回
        if business is not None and self.get_link(db, business.id, document_id) is not None:
            logger.info(
                "自动归集幂等跳过：文档 %s 已关联档案 %s", document_id, business.business_no
            )
            return business

        try:
            # 档案不存在 → 新建（active）
            if business is None:
                business = self._create_record_from_fields(db, document_id, contract_no)
                match_mode = "new"

            # 建立关联
            link = BusinessFile(
                business_id=business.id,
                document_id=document_id,
                file_role=_resolve_file_role(document.document_type),
                link_source=LINK_SOURCE_AUTO_RULE,
                sort_order=len(business.files),
            )
            db.add(link)
            db.flush()
            db.commit()
            log_operation(
                db,
                OP_BUSINESS_AUTO_LINK,
                old_path="",
                new_path=business.business_no,
                document_id=document_id,
                result=RESULT_OK,
                error_message=(
                    f"match={match_mode}, confidence={conf:.2f}, "
                    f"file_role={link.file_role}, business_id={business.id}"
                ),
            )
            logger.info(
                "自动归集完成：文档 %s（%s）→ 档案 %s（role=%s）",
                document_id, document.original_filename, business.business_no, link.file_role,
            )
            return business
        except IntegrityError:
            # 并发/重复插入被唯一约束拦截 → 回滚后按已关联处理
            db.rollback()
            logger.warning("自动归集冲突回滚：文档 %s 与档案 %s 已关联", document_id, contract_no)
            existing = self.get_business_by_no(db, contract_no)
            return existing
        except Exception as exc:  # noqa: BLE001 - 单文档失败不中断批量回填
            db.rollback()
            logger.exception("自动归集失败：document_id=%s: %s", document_id, exc)
            log_operation(
                db,
                OP_BUSINESS_AUTO_LINK,
                old_path="",
                new_path=contract_no,
                document_id=document_id,
                result=RESULT_FAILED,
                error_message=str(exc)[:500],
            )
            return None

    def _find_fuzzy_business(
        self, db: Session, contract_no: str
    ) -> tuple[str, Optional[BusinessRecord]]:
        """归一化精确匹配未命中时，检测是否存在高度相似的既有档案。

        手写合同号后四位 OCR 识别错（如 YC0453 → YC0458）时精确匹配会失败；
        此时若存在高相似档案，系统无法区分"识别错误"与"真实新业务号"
        （两个独立业务号本就可能只差一位），因此一律不自动处理，交由人工确认。

        返回 (state, record)：
          ("none", None)       无相似候选 → 可安全新建档案
          ("candidate", rec)  唯一高相似候选 → 暂停自动归集，待人工确认
          ("ambiguous", None) 多个候选过近 → 暂停自动归集，待人工确认
        """
        norm = _normalize_no(contract_no)
        scored: list[tuple[float, BusinessRecord]] = []
        for rec in db.execute(select(BusinessRecord)).scalars():
            ratio = SequenceMatcher(None, norm, _normalize_no(rec.business_no)).ratio()
            if ratio >= _FUZZY_THRESHOLD:
                scored.append((ratio, rec))
        if not scored:
            return ("none", None)
        scored.sort(key=lambda x: x[0], reverse=True)
        if len(scored) >= 2 and scored[0][0] - scored[1][0] < _FUZZY_AMBIGUITY_GAP:
            logger.info(
                "模糊匹配歧义（前两名相似度过近），暂停自动归集待人工确认: %r -> %s",
                contract_no, [(f"{r[1].business_no}:{r[0]:.2f}") for r in scored[:3]],
            )
            return ("ambiguous", None)
        logger.info(
            "模糊匹配候选，暂停自动归集待人工确认: %r -> %s (%.2f)",
            contract_no, scored[0][1].business_no, scored[0][0],
        )
        return ("candidate", scored[0][1])

    def _create_record_from_fields(
        self, db: Session, document_id: int, contract_no: str
    ) -> BusinessRecord:
        """用文档提取字段新建业务档案（active），字段缺失留空不报错。"""
        document = db.get(Document, document_id)
        fields = {
            name: _get_field(db, document_id, name) for name in _RECORD_FIELD_MAP
        }
        record = BusinessRecord(
            business_no=contract_no,
            title=document.title or f"{contract_no} 业务档案",
            business_type=document.document_type or "",
            status=BUSINESS_STATUS_ACTIVE,
            ship_name=fields.get("vessel", ""),
            counterparty=fields.get("company", ""),
            total_amount=_parse_amount(fields.get("amount", "")),
            sign_date=_parse_date(fields.get("date", "")),
        )
        db.add(record)
        db.flush()
        logger.info("新建业务档案：%s（来源文档 %s）", contract_no, document_id)
        return record

    # ---------- 2. 手动绑定 ----------

    def manual_link_file(
        self, db: Session, business_id: int, document_id: int, file_role: str
    ) -> BusinessFile:
        """手动把文档绑定到指定档案。

        校验：档案与文档必须存在；重复绑定抛 BusinessArchiveError。
        返回：新建的关联记录。
        """
        business = self.get_business(db, business_id)
        if business is None:
            raise BusinessArchiveError(f"业务档案不存在 business_id={business_id}")
        document = db.get(Document, document_id)
        if document is None:
            raise BusinessArchiveError(f"文档不存在 document_id={document_id}")
        if self.get_link(db, business_id, document_id) is not None:
            raise BusinessArchiveError(
                f"文档 {document_id} 已关联到档案 {business.business_no}，请先解除"
            )

        link = BusinessFile(
            business_id=business_id,
            document_id=document_id,
            file_role=file_role or FILE_ROLE_OTHER,
            link_source=LINK_SOURCE_MANUAL,
            sort_order=len(business.files),
        )
        try:
            db.add(link)
            db.flush()
            db.commit()
            log_operation(
                db,
                OP_BUSINESS_MANUAL_LINK,
                old_path="",
                new_path=f"{business.business_no}#{file_role}",
                document_id=document_id,
                result=RESULT_OK,
            )
            logger.info(
                "手动绑定完成：文档 %s → 档案 %s（role=%s）",
                document_id, business.business_no, link.file_role,
            )
            return link
        except IntegrityError:
            db.rollback()
            raise BusinessArchiveError(
                f"文档 {document_id} 与档案 {business_id} 重复关联（唯一约束）"
            ) from None
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("手动绑定失败：business_id=%s document_id=%s: %s", business_id, document_id, exc)
            raise BusinessArchiveError(f"手动绑定失败：{exc}") from exc

    # ---------- 3. 解除关联 ----------

    def unlink_file(self, db: Session, business_id: int, document_id: int) -> bool:
        """解除文档与档案的关联（不删除物理文档与 documents 记录）。

        返回：True 表示已解除；关联不存在返回 False（幂等）。
        """
        link = self.get_link(db, business_id, document_id)
        if link is None:
            logger.warning(
                "解除关联跳过：文档 %s 与档案 %s 无关联", document_id, business_id
            )
            return False
        business = self.get_business(db, business_id)
        try:
            db.delete(link)
            db.commit()
            log_operation(
                db,
                OP_BUSINESS_UNLINK,
                old_path=business.business_no if business else "",
                new_path="",
                document_id=document_id,
                result=RESULT_OK,
            )
            logger.info("解除关联完成：文档 %s ↔ 档案 %s", document_id, business_id)
            return True
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("解除关联失败：business_id=%s document_id=%s: %s", business_id, document_id, exc)
            log_operation(
                db,
                OP_BUSINESS_UNLINK,
                old_path="",
                new_path="",
                document_id=document_id,
                result=RESULT_FAILED,
                error_message=str(exc)[:500],
            )
            return False

    # ---------- 4. 历史数据回填 ----------

    def backfill_historical_data(self, db: Session) -> dict:
        """扫描全部历史文档（排除回收站），批量自动归集建档。

        返回统计：
            scanned             扫描文档数（排除回收站）
            linked              本次新建立关联数
            already_linked      回填前已有档案关联的幂等跳过数
            skipped_no_contract 无合同号跳过数
            failed              归集失败数
            created_records     本次新建档案数（回填前后档案总数差）
        """
        stats = {
            "scanned": 0,
            "linked": 0,
            "already_linked": 0,
            "skipped_no_contract": 0,
            "failed": 0,
            "created_records": 0,
        }
        # 回填前快照：档案总数 + 已有关联文档集合（幂等判断基准）
        records_before = (
            db.execute(select(func.count()).select_from(BusinessRecord)).scalar() or 0
        )
        linked_doc_ids: set[int] = set(
            db.execute(select(BusinessFile.document_id)).scalars()
        )

        documents = db.execute(
            select(Document.id)
            .where(Document.status != STATUS_RECYCLED)
            .order_by(Document.id.asc())
        ).scalars()

        for doc_id in documents:
            stats["scanned"] += 1
            contract_no = _get_field(db, doc_id, _BUSINESS_NO_FIELD)
            if not contract_no:
                # 与 auto_link_document 一致：字段缺失时从文件名回退提取
                # （文件名规则直接归档的合同无字段，但文件名含合同号）
                doc = db.get(Document, doc_id)
                contract_no = _extract_no_from_filename(
                    doc.original_filename if doc else ""
                )
            if not contract_no:
                stats["skipped_no_contract"] += 1
                continue
            try:
                business = self.auto_link_document(db, doc_id)
                if business is None:
                    stats["failed"] += 1
                elif doc_id in linked_doc_ids:
                    stats["already_linked"] += 1
                else:
                    stats["linked"] += 1
                    linked_doc_ids.add(doc_id)
            except Exception as exc:  # noqa: BLE001 - 单文档失败不中断批量回填
                stats["failed"] += 1
                logger.exception("回填失败：document_id=%s: %s", doc_id, exc)

        records_after = (
            db.execute(select(func.count()).select_from(BusinessRecord)).scalar() or 0
        )
        stats["created_records"] = max(records_after - records_before, 0)
        logger.info(
            "历史数据回填完成：scanned=%d linked=%d already=%d skipped=%d failed=%d created=%d",
            stats["scanned"], stats["linked"], stats["already_linked"],
            stats["skipped_no_contract"], stats["failed"], stats["created_records"],
        )
        return stats