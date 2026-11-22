"""对账 ↔ 业务档案的双向桥。

两个模块的主实体粒度不同，这是历史设计而非缺陷：

- 对账：一条**业务线** = 一采购合同 + 一销售合同，用台账 O 列业务线号（YWX…）标识，
  闭环发生在两张合同之间，所以主实体必须在业务线这一层；
- 档案：一个**档案** = 一个合同号（HSYCCG-0321），按合同收口文件。

桥的职责：
1. 补单落在业务线槽位上时，按方向挑对应合同号，写进档案（`archive_document`）；
2. 档案侧新增文件后，反查该合同号属于哪条业务线并重算对账（`recompute_for_contract`）；
3. 存量回填：把对账矩阵里已确认归属的单据批量补进档案（`backfill_from_reports`）。

注意 `business_files.file_role` 会被 `_load_documents` 当作 `file_role` 读回去，
所以这里写进去的角色必须是对账侧能认的值（payment_receipt 映射成 other，
`infer_role` 仍会靠 document_type/文件名判成回单，不会丢）。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import BusinessFile, BusinessRecord, Document, ReconciliationReport
from backend.services.business_archive_service import _normalize_no

logger = logging.getLogger(__name__)

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"

# 对账角色 → 档案 file_role
_ARCHIVE_ROLE = {
    "contract": "contract",
    "settlement": "settlement",
    "invoice": "invoice",
    "cargo_right": "cargo_right",
    "payment_receipt": "other",
}

SOURCE_MANUAL = "manual"
SOURCE_RECON_LEDGER = "recon_ledger"
# 由对账补单带进档案的关联：取消对账归属时要连它一起撤，否则单据
# 会因为档案里的合同号被自动绑定，永远回不到「未归属候选」
SOURCE_RECON_LINK = "recon_link"


# ---------- 台账读取 ----------


def ledger_businesses(db: Session):
    from backend.services.reconciliation_service import LedgerReader, ReconciliationService

    src = ReconciliationService(db).resolve_source(None)
    reader = LedgerReader(src.file_path)
    try:
        return reader.load()
    finally:
        reader.close()


def ledger_business(db: Session, business_no: str):
    from backend.services.reconciliation_service import norm_no

    target = norm_no(business_no)
    for biz in ledger_businesses(db):
        if biz.business_no == target:
            return biz
    return None


def contract_nos_for(biz, direction: str) -> list[str]:
    """该业务线在档案侧可用的合同号，按优先级排列。

    台账一行会登记两套编号体系的号（SJWLCG… 与 BOTC… 这类 OA 号），
    它们是**同一份合同**的别名，档案只能有一个，所以要把候选全给出来。
    """
    if biz is None:
        return []
    if direction == DIRECTION_UP:
        nos = list(biz.up_contract_nos or [])
        if biz.up_contract_no:
            nos.insert(0, biz.up_contract_no)
    elif direction == DIRECTION_DOWN:
        nos = list(biz.down_contract_nos or [])
        if biz.down_contract_no:
            nos.insert(0, biz.down_contract_no)
    else:
        nos = list(biz.up_contract_nos or []) + list(biz.down_contract_nos or [])
        for no in (biz.up_contract_no, biz.down_contract_no):
            if no:
                nos.insert(0, no)
    seen: set[str] = set()
    out: list[str] = []
    for no in nos:
        if not no or no in seen:
            continue
        seen.add(no)
        out.append(no)
    return out


def pick_contract_no(biz, direction: str) -> str:
    """按方向取该业务线在档案侧的合同号（只看台账，不看已有档案）。"""
    nos = contract_nos_for(biz, direction)
    return nos[0] if nos else ""


# ---------- 档案写入 ----------


def _find_record(db: Session, contract_no: str) -> Optional[BusinessRecord]:
    rec = db.execute(
        select(BusinessRecord).where(BusinessRecord.business_no == contract_no)
    ).scalar_one_or_none()
    if rec is not None:
        return rec
    # 编号写法差异（全半角括号、DD 段）靠归一化再找一遍
    norm = _normalize_no(contract_no)
    for candidate in db.execute(select(BusinessRecord)).scalars():
        if _normalize_no(candidate.business_no) == norm:
            return candidate
    return None


def _create_record(db: Session, contract_no: str, biz, direction: str) -> BusinessRecord:
    from decimal import Decimal

    up = direction == DIRECTION_UP
    amount = None
    if biz is not None:
        amount = biz.up_contract_amount if up else biz.down_contract_amount
    rec = BusinessRecord(
        business_no=contract_no,
        title=contract_no,
        business_type="采购" if up else ("销售" if direction == DIRECTION_DOWN else ""),
        status="active",
        ship_name=(biz.vessel or "")[:255] if biz is not None else "",
        counterparty=(biz.supplier if up else biz.customer) if biz is not None else "",
        total_amount=amount if amount is not None else Decimal("0"),
        extra_data=json.dumps(
            {
                "business_line": biz.business_no if biz is not None else "",
                "source": "recon_bridge",
                "material": biz.material if biz is not None else "",
            },
            ensure_ascii=False,
        ),
    )
    db.add(rec)
    db.flush()
    return rec


def archive_document(
    db: Session,
    document_id: int,
    *,
    business_no: str,
    direction: str,
    role: str,
    source: str = SOURCE_MANUAL,
) -> dict:
    """把一份单据按业务线+方向归集进档案。

    返回 {ok, action, contract_no, business_id, reason}：
    - action = created / updated / exists / conflict
    - conflict：单据已人工归到另一个档案，不动它（避免把别条业务线的资料搬走）
    """
    doc = db.get(Document, document_id)
    if doc is None:
        return {"ok": False, "action": "skip", "reason": "文档不存在"}

    biz = ledger_business(db, business_no)
    if biz is None:
        return {"ok": False, "action": "skip", "reason": f"台账中没有业务线 {business_no}"}

    candidates = contract_nos_for(biz, direction)
    if not candidates:
        return {
            "ok": False,
            "action": "skip",
            "reason": "该业务线在这个方向上没有登记合同号，先补台账",
        }

    archive_role = _ARCHIVE_ROLE.get(role, "other")
    # 先看这些别名里有没有已经建过档案的，有就复用 —— 同一份合同不能被两套编号
    # 各建一个档案（台账一行会同时登 SJWL/CG 号与 OA 号）
    rec = None
    contract_no = candidates[0]
    for no in candidates:
        found = _find_record(db, no)
        if found is not None:
            rec = found
            contract_no = found.business_no
            break
    created = False
    if rec is None:
        rec = _create_record(db, contract_no, biz, direction)
        created = True

    links = db.execute(
        select(BusinessFile).where(BusinessFile.document_id == document_id)
    ).scalars().all()
    same = next((x for x in links if x.business_id == rec.id), None)
    if same is not None:
        changed = False
        if same.file_role != archive_role:
            same.file_role = archive_role
            changed = True
        if source == SOURCE_MANUAL and same.link_source != SOURCE_MANUAL:
            same.link_source = SOURCE_MANUAL
            changed = True
        if changed:
            db.commit()
            return {
                "ok": True, "action": "updated", "contract_no": contract_no,
                "business_id": rec.id, "created_record": created,
            }
        return {
            "ok": True, "action": "exists", "contract_no": contract_no,
            "business_id": rec.id, "created_record": created,
        }

    others = [x for x in links if x.business_id != rec.id]
    if any(x.link_source == SOURCE_MANUAL for x in others):
        return {
            "ok": False, "action": "conflict", "contract_no": contract_no,
            "business_id": rec.id,
            "reason": "这份单据已人工归集到另一个档案，先在那里取消归集",
        }
    primary = archive_role == "contract" and not db.execute(
        select(BusinessFile.id).where(
            BusinessFile.business_id == rec.id, BusinessFile.file_role == "contract"
        ).limit(1)
    ).first()
    db.add(BusinessFile(
        business_id=rec.id,
        document_id=document_id,
        file_role=archive_role,
        link_source=source,
        is_primary=bool(primary),
    ))
    db.commit()
    return {
        "ok": True, "action": "created", "contract_no": contract_no,
        "business_id": rec.id, "created_record": created,
    }


def unarchive_document(db: Session, document_id: int) -> int:
    """撤掉「对账补单」带进档案的关联；用户手工归集的不动。

    不撤会有个死循环：档案里的合同号会被 `_load_documents` 当成文档自己的
    合同号，于是取消关联后单据仍然带着合同号，永远回不到未归属候选。
    """
    rows = db.execute(
        select(BusinessFile).where(
            BusinessFile.document_id == document_id,
            BusinessFile.link_source == SOURCE_RECON_LINK,
        )
    ).scalars().all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


# ---------- 反查与重算 ----------


def businesses_of_contract(db: Session, contract_no: str) -> list[str]:
    """一个合同号可能对应多条业务线（少见，但同一合同分多批执行时存在）。"""
    from backend.services.reconciliation_service import _all_contract_nos, contract_variants

    wanted = contract_variants(contract_no)
    if not wanted:
        return []
    hits: list[str] = []
    for biz in ledger_businesses(db):
        nos = set()
        for no in _all_contract_nos(biz):
            nos |= contract_variants(no)
        if wanted & nos:
            hits.append(biz.business_no)
    return hits


def recompute_for_contract(db: Session, contract_no: str) -> list[str]:
    """档案新增文件后，把涉及的业务线重算一遍；返回重算的业务线号。"""
    from backend.services.reconciliation_service import ReconciliationError, ReconciliationService

    done: list[str] = []
    for business_no in businesses_of_contract(db, contract_no):
        try:
            ReconciliationService(db).run(business_no=business_no)
            done.append(business_no)
        except ReconciliationError as exc:
            logger.warning("档案变更后重算 %s 失败：%s", business_no, exc)
    return done


# ---------- 存量回填 ----------


def backfill_from_reports(db: Session) -> dict:
    """把对账矩阵里已归属的单据补进档案（只补没归集的，不动人工归属）。"""
    stats = {"scanned": 0, "created": 0, "updated": 0, "exists": 0,
             "conflict": 0, "skip": 0, "new_records": 0}
    seen: set[tuple[str, int]] = set()
    for report in db.execute(select(ReconciliationReport)).scalars().all():
        try:
            payload = json.loads(report.report_json or "{}")
        except ValueError:
            continue
        for cell in payload.get("matrix") or []:
            direction = cell.get("direction") or ""
            role = cell.get("role") or ""
            for doc_id in cell.get("document_ids") or []:
                key = (report.business_no, int(doc_id))
                if key in seen:
                    continue
                seen.add(key)
                stats["scanned"] += 1
                res = archive_document(
                    db,
                    int(doc_id),
                    business_no=report.business_no,
                    direction=direction,
                    role=role,
                    source=SOURCE_RECON_LEDGER,
                )
                action = res.get("action", "skip")
                stats[action] = stats.get(action, 0) + 1
                if res.get("created_record"):
                    stats["new_records"] += 1
    return stats
