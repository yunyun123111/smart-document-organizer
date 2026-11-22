"""业务线闭环判定。

一条业务线从上游采购到下游销售，两侧各自成环：
    合同 → 结算单 → 发票 → 资金凭证（上游付款回单 / 下游回款回单）

只有「资料齐 + 金额平 + 资金无欠款无余款」才算这一侧闭环；两侧都闭环，
整条业务线才算闭环。中途缺件停在「缺资料/执行中」，不再笼统报「有差异」。

设计要点：
- 纯函数，只读台账口径（biz）与对账矩阵（matrix），不查库、不依赖服务层常量，
  便于单测，也避免与 reconciliation_service 循环引用。
- 货权单/出库单不属于金额闭环要件，只作为可选资料统计，不计入完成度。
- 台账没登记某一金额时，该项金额比对记为「不可判定」，不阻断闭环，
  但要写进 notes 提醒补登记 —— 缺口径不等于已核对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

TOLERANCE = Decimal("0.01")
_ZERO = Decimal("0")

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"

ROLE_CONTRACT = "contract"
ROLE_SETTLEMENT = "settlement"
ROLE_INVOICE = "invoice"
ROLE_PAYMENT_RECEIPT = "payment_receipt"
ROLE_CARGO_RIGHT = "cargo_right"

# 闭环阶段
STAGE_NOT_STARTED = "not_started"
STAGE_COLLECTING = "collecting"
STAGE_UNBALANCED = "unbalanced"
STAGE_PARTIAL = "partial"
STAGE_CLOSED = "closed"

STAGE_LABELS = {
    STAGE_NOT_STARTED: "未开始",
    STAGE_COLLECTING: "缺资料",
    STAGE_UNBALANCED: "金额待平",
    STAGE_PARTIAL: "单侧闭环",
    STAGE_CLOSED: "已闭环",
}

# 每侧的 4 个金额闭环要件：(角色, 显示名)
REQUIRED_ROLES = (
    (ROLE_CONTRACT, "合同"),
    (ROLE_SETTLEMENT, "结算单"),
    (ROLE_INVOICE, "发票"),
    (ROLE_PAYMENT_RECEIPT, "资金凭证"),
)

_SIDE_CONFIG = {
    DIRECTION_UP: {
        "label": "上游",
        "contract_attr": "up_contract_no",
        "settlement_attr": "up_settlement",
        "fund_attr": "pay_amount",
        "fund_label": "付款回单",
        "fund_attr_cn": "付款额",
        "fund_ledger_label": "台账付款额",
        "no_contract_reason": "上游合同号为空（未建采购线）",
    },
    DIRECTION_DOWN: {
        "label": "下游",
        "contract_attr": "down_contract_no",
        "settlement_attr": "down_settlement",
        "fund_attr": "receipt_amount",
        "fund_label": "回款回单",
        "fund_attr_cn": "应收额",
        "fund_ledger_label": "台账应收额",
        "no_contract_reason": "下游合同号为空（待双签或未生成）",
    },
}


def _dec(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _fmt(value: Optional[Decimal]) -> str:
    return f"{value:,.2f}" if value is not None else "—"


@dataclass
class Requirement:
    role: str
    label: str
    count: int = 0
    amount: Optional[Decimal] = None
    target: Optional[Decimal] = None
    target_label: str = ""
    amount_ok: Optional[bool] = None

    @property
    def have(self) -> bool:
        return self.count > 0

    @property
    def ok(self) -> bool:
        return self.have and self.amount_ok is not False

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "label": self.label,
            "count": self.count,
            "have": self.have,
            "amount": float(self.amount) if self.amount is not None else None,
            "target": float(self.target) if self.target is not None else None,
            "target_label": self.target_label,
            "amount_ok": self.amount_ok,
            "ok": self.ok,
        }


@dataclass
class SideResult:
    direction: str
    label: str
    applicable: bool = True
    requirements: list[Requirement] = field(default_factory=list)
    optional: dict = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def docs_ok(self) -> bool:
        return all(r.have for r in self.requirements)

    @property
    def amount_ok(self) -> bool:
        """要件金额是否全部勾稽通过。

        合同不比金额（None 是设计如此）；其余要件必须「明确相等」才算平，
        金额没识别出来（None）按未通过处理 —— 没数就等于没核对过。
        """
        for r in self.requirements:
            if r.role == ROLE_CONTRACT:
                continue
            if r.amount_ok is not True:
                return False
        return True

    @property
    def closed(self) -> bool:
        return bool(self.applicable and self.docs_ok and self.amount_ok
                    and not self.blockers)

    @property
    def percent(self) -> int:
        if not self.requirements:
            return 0
        done = sum(1 for r in self.requirements if r.ok)
        return int(round(done / len(self.requirements) * 100))

    @property
    def stage(self) -> str:
        if not self.applicable:
            return STAGE_NOT_STARTED
        if self.closed:
            return STAGE_CLOSED
        if not any(r.have for r in self.requirements):
            return STAGE_NOT_STARTED
        if self.docs_ok and not self.amount_ok:
            return STAGE_UNBALANCED
        return STAGE_COLLECTING

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "label": self.label,
            "applicable": self.applicable,
            "closed": self.closed,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage),
            "percent": self.percent,
            "docs_ok": self.docs_ok,
            "amount_ok": self.amount_ok,
            "requirements": [r.to_dict() for r in self.requirements],
            "optional": dict(self.optional),
            "blockers": list(self.blockers),
            "notes": list(self.notes),
        }


def _matrix_index(matrix: list) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for cell in matrix:
        direction = getattr(cell, "direction", None) or (cell.get("direction") if isinstance(cell, dict) else None)
        role = getattr(cell, "role", None) or (cell.get("role") if isinstance(cell, dict) else None)
        if direction and role:
            out[(direction, role)] = cell
    return out


def _get(cell: Any, name: str, default: Any = None) -> Any:
    if cell is None:
        return default
    if isinstance(cell, dict):
        return cell.get(name, default)
    return getattr(cell, name, default)


def _side(biz: Any, cells: dict, direction: str) -> SideResult:
    cfg = _SIDE_CONFIG[direction]
    label = cfg["label"]
    contract_no = getattr(biz, cfg["contract_attr"], "") or ""
    settlement = _dec(getattr(biz, cfg["settlement_attr"], None))
    fund_ledger = _dec(getattr(biz, cfg["fund_attr"], None))

    has_any = any(
        int(_get(cells.get((direction, role)), "count", 0) or 0) > 0
        for role in (ROLE_CONTRACT, ROLE_SETTLEMENT, ROLE_INVOICE, ROLE_PAYMENT_RECEIPT)
    )
    side = SideResult(
        direction=direction,
        label=label,
        applicable=bool(contract_no) or has_any,
    )
    if not side.applicable:
        side.notes.append(cfg["no_contract_reason"])

    targets = {
        ROLE_SETTLEMENT: (settlement, "台账结算额"),
        ROLE_INVOICE: (settlement, "台账结算额"),
        ROLE_PAYMENT_RECEIPT: (
            fund_ledger if fund_ledger is not None else settlement,
            cfg["fund_ledger_label"] if fund_ledger is not None else "台账结算额",
        ),
    }

    for role, role_label in REQUIRED_ROLES:
        cell = cells.get((direction, role))
        count = int(_get(cell, "count", 0) or 0)
        amount = _dec(_get(cell, "amount", None))
        # 矩阵的金额缺省是 0.0：有单据但金额为 0，实际是「没识别出来」。
        # 当成 0 元参与勾稽会误报「差 100 万」，所以按不可判定处理。
        # 没单据更谈不上金额，一律 None。
        if count == 0 or (amount is not None and amount == _ZERO):
            amount = None
        target, target_label = targets.get(role, (None, ""))
        amount_ok: Optional[bool] = None
        if amount is not None and target is not None:
            amount_ok = abs(amount - target) <= TOLERANCE
        # 资金凭证用这一侧的具体叫法（付款回单 / 回款回单），
        # 「资金凭证」只是内部角色名，展示时说人话。
        display_label = cfg["fund_label"] if role == ROLE_PAYMENT_RECEIPT else role_label
        side.requirements.append(Requirement(
            role=role, label=display_label, count=count, amount=amount,
            target=target, target_label=target_label, amount_ok=amount_ok,
        ))

    cargo = cells.get((direction, ROLE_CARGO_RIGHT))
    side.optional["cargo_right"] = int(_get(cargo, "count", 0) or 0)

    # 台账没登记这一侧的实付/实收额：退一步按结算额比对，保证凭证仍能判定，
    # 但这个口径是推出来的，要留一条 note 让人确认。
    if fund_ledger is None and settlement is not None:
        side.notes.append(
            f"台账未登记{cfg['fund_attr_cn']}，{cfg['fund_label']}改按结算额比对"
        )

    # 逐项写 blocker：缺件、金额不符、口径缺失
    for r in side.requirements:
        if not r.have:
            side.blockers.append(f"{label}缺{r.label}")
            continue
        if r.amount_ok is False:
            delta = (r.amount or _ZERO) - (r.target or _ZERO)
            side.blockers.append(
                f"{label}{r.label}合计 {_fmt(r.amount)} 与{r.target_label} "
                f"{_fmt(r.target)} 差 {_fmt(delta)}"
            )
        elif r.amount_ok is None and r.role != ROLE_CONTRACT:
            side.notes.append(
                f"{label}{r.label}金额未参与勾稽（单据或台账缺金额口径）"
            )

    return side


def compute_closure(biz: Any, matrix: list) -> dict:
    """算出这条业务线的闭环状态。

    biz：台账业务线（读 up_contract_no/up_settlement/pay_amount/down_* 等属性）
    matrix：对账矩阵（10 格，含 direction/role/count/amount）

    返回可直接放进报告 payload 的 dict。
    """
    cells = _matrix_index(matrix)
    sides = [_side(biz, cells, d) for d in (DIRECTION_UP, DIRECTION_DOWN)]
    has_any_doc = any(int(_get(c, "count", 0) or 0) > 0
                      for c in cells.values())
    applicable = [s for s in sides if s.applicable]

    total = sum(len(s.requirements) for s in applicable)
    done = sum(sum(1 for r in s.requirements if r.ok) for s in applicable)
    percent = int(round(done / total * 100)) if total else 0

    if not has_any_doc:
        stage = STAGE_NOT_STARTED
    elif not applicable:
        stage = STAGE_NOT_STARTED
    elif all(s.closed for s in applicable):
        stage = STAGE_CLOSED
    elif all(s.docs_ok for s in applicable):
        # 资料全齐但没全闭合，只剩金额问题
        stage = STAGE_UNBALANCED
    elif len(applicable) > 1 and any(s.closed for s in applicable):
        stage = STAGE_PARTIAL
    else:
        stage = STAGE_COLLECTING

    blockers = [b for s in sides if s.applicable for b in s.blockers]
    if stage == STAGE_CLOSED:
        blockers = []
        summary = "上下游资料齐全、金额勾稽一致、无欠款无余款"
    elif stage == STAGE_NOT_STARTED:
        summary = "尚未上传单据资料，无法判定闭环"
    elif stage == STAGE_PARTIAL:
        closed_side = next(s.label for s in applicable if s.closed)
        open_side = next(s.label for s in applicable if not s.closed)
        summary = f"{closed_side}已闭环，{open_side}尚未闭环"
    elif stage == STAGE_UNBALANCED:
        detail = "；".join(blockers[:3]) or "；".join(
            n for s in sides if s.applicable for n in s.notes
        ) or "金额口径缺失，无法判定"
        summary = "资料已齐，金额勾稽未平：" + detail
    else:
        summary = "资料未齐：" + "；".join(blockers[:3])

    return {
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "closed": stage == STAGE_CLOSED,
        "percent": percent,
        "requirements_total": total,
        "requirements_done": done,
        "sides": {s.direction: s.to_dict() for s in sides},
        "blockers": blockers,
        "summary": summary,
    }
