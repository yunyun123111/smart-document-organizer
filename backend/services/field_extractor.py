"""字段提取器 v2 —— 多规则并行规则引擎（规格书 Phase 6 升级）。

从「每字段单规则（上下文 → 通用兜底）」升级为：
1. 多规则并行匹配（正则 + 关键词 + 发票模板 + 文件名）
2. 字段优先级体系（每条规则带 priority，高优先级裁决）
3. 字段冲突检测（同字段多个不同候选值 → 记录冲突并裁决）
4. 字段缺失补全（从文件名提取核心字段）
5. OCR 清洗模块（去页码噪声 / 竖排合并 / 水印重复）

对外接口保持兼容：`FieldExtractor.extract(text, document_type=None, filename=None)`
仍返回 list[ExtractedField]；新增 `last_conflicts` 暴露冲突信息。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.utils.logger import get_logger

logger = get_logger("services.field_extractor")

# 字段来源（与 models.document_field 对齐，额外引入文件名来源）
SOURCE_RULE = "RULE"
SOURCE_FILENAME = "FILENAME"
SOURCE_OCR = "OCR"

# 公司通用词尾
_COMPANY_SUFFIX = (
    "有限公司|有限责任公司|股份有限公司|集团有限公司|集团|公司|工厂|商行|事务所|合作社|医院|学校"
)

# 中文公司名模式：上下文前缀（甲方/乙方/采购方等）+ 名称
_COMPANY_CONTEXT = re.compile(
    r"(?:甲方|乙方|采购方|销售方|供应商|供货方|买方|卖方|付款人|收款人|开票方|购方)[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,40}?(?:有限公司|有限责任公司|股份有限公司|公司|集团|厂))"
)
# 通用公司名
_COMPANY_GENERIC = re.compile(
    rf"([\u4e00-\u9fa5A-Za-z0-9（）()]{{2,30}}(?:{_COMPANY_SUFFIX}))"
)

# 日期：上下文锚定（开票日期/签订日期/交易日期/日期）+ 通用
_DATE_CONTEXT = re.compile(
    r"(?:开票日期|签订日期|交易日期|合同日期|日期|签署日期|出票日期)[:：]?\s*"
    r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?"
)
_DATE_GENERIC = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?")

# 编号类：合同编号 / 订单编号 / 发票号码（值字符集支持全角括号）
_CONTRACT_NO = re.compile(
    r"(?:合同编号|合同号|合同No\.?|Contract\s*(?:No\.?|#)?)[:：]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-_/（）()]{3,39})",
    re.IGNORECASE,
)
# 裸"合同"前缀（如"双方合同SJWLXS（DD）-2026-YC0433下"），负向前瞻排除单价/数量/金额等
_CONTRACT_NO_BARE = re.compile(
    r"(?:双方合同|本合同|双方之?合同|贵我双方合同|合同)(?!单价|数量|金额|编号|号|款|价|期限|内容|约定|总额|条款|条件|总价)"
    r"[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/（）()]{3,39})",
    re.IGNORECASE,
)
_ORDER_NO = re.compile(
    r"(?:订单号|订单编号|订单No\.?|PO\s*(?:No\.?|#)?|采购单号)[:：]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-_/]{2,39})",
    re.IGNORECASE,
)
_INVOICE_NO = re.compile(
    r"(?:发票号码|发票号|发票No\.?|Invoice\s*(?:No\.?|#)?)[:：]?\s*"
    r"([A-Za-z0-9\-_/]{4,30})",
    re.IGNORECASE,
)

# 金额：带货币标识或"元"，支持千分位/小数
_AMOUNT_PATTERNS = [
    re.compile(
        r"(?:价税合计|含税合计|合计金额|含税金额|总金额)[\s\S]{0,40}?"
        r"(?:¥|￥|人民币|RMB)?\s*([\d,]+(?:\.\d{1,2})?)"
    ),
    re.compile(r"(?:人民币|RMB|¥|￥)\s*([\d,]+(?:\.\d{1,2})?)\s*(?:元)?"),
    re.compile(r"(?:金额|价款|货款|总价|交易金额)[:：]?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:元|人民币)?"),
    re.compile(r"([\d,]+(?:\.\d{1,2})?)\s*元"),
]

# 船名：上下文锚定（结算单/货权转移单通常带"船名："，支持数字结尾如 海兴168）
_VESSEL_CONTEXT = re.compile(
    r"(?:船名|船舶名称|船号)[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9（）()\-]{2,40}?)(?=\s|$|[，,;；。])"
)
# 引号内船名（如 "NIGHTSKY夜空"轮）
_VESSEL_QUOTED = re.compile(
    r"[“\"]([\u4e00-\u9fa5A-Za-z0-9\-/（）()]{2,40}?)[”\"]\s*(?:轮|号|船|货轮)"
)
# 物料名称：上下文锚定 + 前瞻截断
_MATERIAL_CONTEXT = re.compile(
    r"(?:物料名称|物料|品名|货物名称|货物品名|商品名称)[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9（）()%\.\-]{2,40}?)(?=\s*(?:数量|单位|规格|单价|备注|吨|[，,;；]|$))"
)
# 吨数后的粉/矿/钢/煤/油类物料（如 10000吨PB粉、500O吨铁矿粉）
_MATERIAL_TON = re.compile(
    r"(?:吨|T|t)\s*([\u4e00-\u9fa5A-Za-z]{1,12}(?:粉|矿|钢|煤|油|砂))"
)
# 数量：上下文锚定 + 单位
_QUANTITY_CONTEXT = re.compile(
    r"(?:数量|吨数|总数量|重量|净重)[:：]?[\s\S]{0,50}?([\d,]+(?:\.\d{1,3})?)\s*(?:吨|T|t|kg|KG|千克|件|个|张|份|批)?"
)

# 发票特征标记（用于触发发票专用提取）
_INVOICE_MARKER = re.compile(r"电子发票|增值税|价税合计|增值税专用发票")

# 销售方：发票/结算单中"销售方信息 名称：XXX公司"
_SELLER_CONTEXT = re.compile(
    r"(?:销售方|销方|卖方|供货方|供应商)(?:信息)?[\s\S]{0,50}?名称[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,40}?(?:有限公司|有限责任公司|股份有限公司|公司|集团|厂))"
)

# ---------- 文件名字段提取（缺失补全） ----------
# 合同号：SJWLXS（DD）-2026-YC0453 / SJWLCG（DD）-2026-0109 / CG20260801
_FN_CONTRACT_NO = re.compile(
    r"([A-Z]{2,10}(?:（DD）|\(DD\)|[-_])?[-_/]?\d{4}[-_/]?[A-Z]{0,6}\d{2,})|"
    r"([A-Z]{2,6}\d{4,})",
    re.IGNORECASE,
)
# 日期：2026-08-14 / 2026_08_14 / 026-08-14
_FN_DATE = re.compile(r"(?<!\d)(\d{4})[-_.年](\d{1,2})[-_.月](\d{1,2})日?|(?<!\d)(\d{2,3})[-_.](\d{1,2})[-_.](\d{1,2})")
# 船名：XXX船（文件名补全）
_FN_VESSEL = re.compile(r"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9]{1,20}?)(?:号|轮)?船")
# 物料：PB粉 / 铁矿粉 / 焦炭 / 螺纹钢 等
_FN_MATERIAL = re.compile(r"([\u4e00-\u9fa5A-Za-z]{1,12}(?:粉|矿|钢|煤|油|砂|碳|料))")


# ---------- OCR 清洗模块 ----------
def _despaghettify(text: str) -> str:
    """合并 OCR 竖排产生的连续单字行（如 购/买/方/信/息 -> 购买方信息）。"""
    lines = text.splitlines()
    out: list[str] = []
    buf = ""
    for ln in lines:
        st = ln.strip()
        if len(st) == 1 and '\u4e00' <= st <= '\u9fa5':
            buf += st
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ln)
    if buf:
        out.append(buf)
    return "\n".join(out)


# 页码/页眉噪声行：`1 / 10`、`第 1 页`、`- 2 -`、纯数字行
_PAGE_NOISE = re.compile(
    r"^\s*(?:第\s*\d+\s*页|页\s*\d+|[-–—]?\s*\d+\s*/\s*\d+\s*[-–—]?|\d+\s*/\s*\d+|\d{1,4}|[-–—]\s*\d+\s*[-–—])\s*$"
)
# 水印/页眉重复词（行首出现的固定弱语义词），连续多行重复才清洗
_HEADER_NOISE = re.compile(
    r"^(仅供参考|仅供\s*内部参考|内部参考|内部资料|机密|秘密|草稿|DRAFT|CONFIDENTIAL|Copy|Page|页)\s*[\s\S]{0,30}$",
    re.IGNORECASE,
)


def clean_ocr_text(text: str) -> str:
    """OCR 文本清洗：竖排合并 + 去页码噪声 + 去水印 + 清理控制字符。

    作为字段提取的前置模块；纯文本/PDF 直提文本同样安全。
    """
    if not text:
        return ""
    text = _despaghettify(text)
    lines = []
    for ln in text.splitlines():
        st = ln.strip()
        if not st:
            continue
        if _PAGE_NOISE.match(st):
            continue
        if _HEADER_NOISE.match(st):
            continue
        lines.append(ln)
    text = "\n".join(lines)
    # 清理不可见控制字符（除 \n \t）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.strip()


# ---------- 候选与冲突 ----------
@dataclass
class FieldCandidate:
    """一次规则命中的字段候选。"""
    value: str
    confidence: float
    source: str = SOURCE_RULE
    rule: str = ""
    priority: float = 0.0


@dataclass
class FieldConflict:
    """同一字段多个不同候选值（冲突）。"""
    field: str
    values: list[str]
    chosen: str
    reason: str = ""


@dataclass
class ExtractedField:
    """一个提取到的字段（对外兼容结构）。"""
    name: str
    value: str
    confidence: float
    matched: list[str] = field(default_factory=list)
    source: str = SOURCE_RULE
    rule: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
        }


def _norm_date(y: str, m: str, d: str) -> str:
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _norm_amount(raw: str) -> str:
    return raw.replace(",", "")


# ---------- 规则表 ----------
# 每个字段：list[matcher]，matcher(text) -> (value, confidence, rule_name, priority, source) | None
# 优先级高的规则在冲突时胜出。
def _m_group(pattern: re.Pattern, group: int = 1, conf: float = 0.85, rule: str = "", prio: float = 0.0, source: str = SOURCE_RULE):
    def _m(text: str):
        m = pattern.search(text)
        if m:
            return m.group(group), conf, rule or pattern.pattern[:20], prio, source
        return None
    return _m


def _m_first_named(pattern: re.Pattern, conf: float, rule: str, prio: float):
    """正则多个命名/序号分组，取第一个非空 group。"""
    def _m(text: str):
        m = pattern.search(text)
        if not m:
            return None
        for g in range(1, len(m.groups()) + 1):
            v = m.group(g)
            if v:
                return v, conf, rule, prio, SOURCE_RULE
        return None
    return _m


# 发票专用（标记命中才生效）
def _invoice_seller_matcher(text: str):
    if not _INVOICE_MARKER.search(text):
        return None
    bank_pos = [m.start() for m in re.finditer(r"开户银行", text)]
    def _near_bank(pos: int, span: int = 25) -> bool:
        return any(abs(pos - b) < span for b in bank_pos)
    names: list[str] = []
    for m in _COMPANY_CONTEXT.finditer(text):
        nm = m.group(1).strip()
        if len(nm) >= 4 and "公司" in nm and not _near_bank(m.start()):
            names.append(nm)
    if not names:
        for m in _COMPANY_GENERIC.finditer(text):
            nm = m.group(1).strip()
            if len(nm) >= 4 and "公司" in nm and not _near_bank(m.start()):
                names.append(nm)
    if names:
        return names[-1], 0.85, "invoice_seller", 85.0, SOURCE_RULE
    return None


def _invoice_amount_matcher(text: str):
    if not _INVOICE_MARKER.search(text):
        return None
    amounts: list[tuple[float, str]] = []
    for v in re.findall(r"\d[\d,]*\.\d{1,2}", text):
        num = float(v.replace(",", ""))
        amounts.append((num, v.replace(",", "")))
    if amounts:
        mx = max(amounts, key=lambda x: x[0])
        return mx[1], 0.9, "invoice_amount", 96.0, SOURCE_RULE
    return None


def _invoice_quantity_matcher(text: str):
    if not _INVOICE_MARKER.search(text):
        return None
    # 优先：数量/吨数上下文锚定（"数量 8000吨"）
    m = _QUANTITY_CONTEXT.search(text)
    if m:
        return _norm_amount(m.group(1)), 0.85, "invoice_quantity", 86.0, SOURCE_RULE
    # 兜底：吨后 单价(3位以上小数) 之后的第一个数值（发票值区版式）
    ton_idx = text.rfind("吨")
    if ton_idx >= 0:
        tail = text[ton_idx:]
        nums = re.findall(r"\d[\d,]*\.?\d*", tail)
        unit_idx = None
        for i, n in enumerate(nums):
            if re.search(r"\.\d{3,}", n):
                unit_idx = i
        if unit_idx is not None and unit_idx + 1 < len(nums):
            qty = nums[unit_idx + 1].replace(",", "")
            if len(qty) <= 10:
                return qty, 0.8, "invoice_quantity", 86.0, SOURCE_RULE
    return None


def _date_context_m(text: str):
    m = _DATE_CONTEXT.search(text)
    if m:
        return _norm_date(m.group(1), m.group(2), m.group(3)), 0.95, "date_context", 100.0, SOURCE_RULE
    return None


def _date_generic_m(text: str):
    m = _DATE_GENERIC.search(text)
    if m:
        return _norm_date(m.group(1), m.group(2), m.group(3)), 0.8, "date_generic", 80.0, SOURCE_RULE
    return None


def _amount_matcher(pattern: re.Pattern, conf: float, rule: str, prio: float):
    def _m(text: str):
        m = pattern.search(text)
        if m:
            return _norm_amount(m.group(1)), conf, rule, prio, SOURCE_RULE
        return None
    return _m


def _quantity_context_m(text: str):
    m = _QUANTITY_CONTEXT.search(text)
    if m:
        return _norm_amount(m.group(1)), 0.85, "quantity_context", 85.0, SOURCE_RULE
    return None


# 文件名专用 matcher（规范化输出 + 至少含一个汉字，避免数字误判）
def _filename_date_matcher(text: str):
    m = _FN_DATE.search(text)
    if not m:
        return None
    # 第一分支：2026-08-14；第二分支：026-08-14（两位年份补 20 前缀）
    if m.group(1):
        return _norm_date(m.group(1), m.group(2), m.group(3)), 0.55, "filename_date", 60.0, SOURCE_FILENAME
    yy = m.group(4)
    if len(yy) == 2:
        yy = "20" + yy
    return _norm_date(yy, m.group(5), m.group(6)), 0.5, "filename_date", 60.0, SOURCE_FILENAME


def _filename_vessel_matcher(text: str):
    m = _FN_VESSEL.search(text)
    if not m:
        return None
    v = m.group(1)
    # 至少含 1 个汉字才算船名（排除纯数字片段误判）
    if v and re.search(r"[\u4e00-\u9fa5]", v):
        return v, 0.55, "filename_vessel", 60.0, SOURCE_FILENAME
    return None


def _filename_material_matcher(text: str):
    m = _FN_MATERIAL.search(text)
    if m:
        return m.group(1), 0.55, "filename_material", 60.0, SOURCE_FILENAME
    return None


# 字段规则表：field -> [matcher, ...]（按优先级从高到低）
_FIELD_RULES: dict[str, list] = {
    "company": [
        _m_group(_COMPANY_CONTEXT, 1, 0.9, "company_context", 100.0),
        _m_group(_COMPANY_GENERIC, 1, 0.7, "company_generic", 70.0),
    ],
    "seller": [
        _m_group(_SELLER_CONTEXT, 1, 0.9, "seller_context", 90.0),
        _invoice_seller_matcher,
    ],
    "date": [
        _date_context_m,
        _date_generic_m,
    ],
    "contract_no": [
        _m_group(_CONTRACT_NO, 1, 0.85, "contract_context", 100.0),
        _m_group(_CONTRACT_NO_BARE, 1, 0.8, "contract_bare", 85.0),
    ],
    "order_no": [
        _m_group(_ORDER_NO, 1, 0.85, "order_context", 100.0),
    ],
    "invoice_no": [
        _m_group(_INVOICE_NO, 1, 0.85, "invoice_context", 100.0),
    ],
    "amount": [
        _invoice_amount_matcher,
        _amount_matcher(_AMOUNT_PATTERNS[0], 0.95, "amount_total", 95.0),
        _amount_matcher(_AMOUNT_PATTERNS[1], 0.9, "amount_currency", 90.0),
        _amount_matcher(_AMOUNT_PATTERNS[2], 0.75, "amount_context", 75.0),
        _amount_matcher(_AMOUNT_PATTERNS[3], 0.6, "amount_yuan", 60.0),
    ],
    "vessel": [
        _m_group(_VESSEL_CONTEXT, 1, 0.85, "vessel_context", 85.0),
        _m_group(_VESSEL_QUOTED, 1, 0.8, "vessel_quoted", 80.0),
    ],
    "material": [
        _m_group(_MATERIAL_CONTEXT, 1, 0.8, "material_context", 80.0),
        _m_group(_MATERIAL_TON, 1, 0.7, "material_ton", 70.0),
    ],
    "quantity": [
        _invoice_quantity_matcher,
        _quantity_context_m,
    ],
}

# 文件名补全字段：缺失时从文件名提取（低优先级，source=FILENAME）
_FILENAME_RULES: dict[str, list] = {
    "contract_no": [
        _m_first_named(_FN_CONTRACT_NO, 0.55, "filename_contract", 60.0),
    ],
    "date": [
        _filename_date_matcher,
    ],
    "vessel": [
        _filename_vessel_matcher,
    ],
    "material": [
        _filename_material_matcher,
    ],
}

# 字段展示顺序（保持历史顺序稳定）
_FIELD_ORDER = ["company", "seller", "date", "contract_no", "order_no", "invoice_no",
                "amount", "vessel", "material", "quantity"]


class FieldExtractor:
    """多规则并行的字段提取引擎。"""

    def __init__(self):
        self.last_conflicts: list[FieldConflict] = []

    # ---------- 主入口 ----------
    def extract(
        self,
        text: str | None,
        document_type: str | None = None,
        filename: str | None = None,
    ) -> list[ExtractedField]:
        """从清洗后的文本中提取字段（可附文件名做缺失补全）。"""
        self.last_conflicts = []
        text = clean_ocr_text(text or "")
        if not text and not filename:
            return []

        # 1) 多规则并行匹配：收集每字段候选
        candidates: dict[str, list[FieldCandidate]] = {}
        for fname, matchers in _FIELD_RULES.items():
            for m in matchers:
                try:
                    hit = m(text)
                except Exception as e:  # noqa: BLE001
                    logger.warning("规则匹配异常 field=%s: %s", fname, e)
                    continue
                if not hit:
                    continue
                value, conf, rule, prio, source = hit
                candidates.setdefault(fname, []).append(
                    FieldCandidate(str(value).strip(), conf, source, rule, prio)
                )

        # 2) 冲突检测 + 优先级裁决
        resolved: dict[str, FieldCandidate] = {}
        for fname, cands in candidates.items():
            # 同值合并（取最高置信度）
            merged: dict[str, FieldCandidate] = {}
            for c in cands:
                key = c.value
                if key not in merged or (c.confidence, c.priority) > (merged[key].confidence, merged[key].priority):
                    merged[key] = c
            # 按 (priority, confidence) 降序裁决
            ordered = sorted(merged.values(), key=lambda c: (c.priority, c.confidence), reverse=True)
            best = ordered[0]
            resolved[fname] = best
            if len(ordered) > 1:
                others = [c.value for c in ordered[1:] if c.value != best.value]
                if others:
                    self.last_conflicts.append(
                        FieldConflict(field=fname, values=[best.value] + others, chosen=best.value,
                                      reason=f"多规则命中不同值，按优先级取 {best.rule}")
                    )

        # 3) 字段缺失补全（从文件名）
        if filename:
            stem = str(filename).strip()
            for fname, rules in _FILENAME_RULES.items():
                if fname in resolved:
                    continue
                for m in rules:
                    try:
                        hit = m(stem)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("文件名规则异常 field=%s: %s", fname, e)
                        continue
                    if not hit:
                        continue
                    value, conf, rule, prio, source = hit
                    v = str(value).strip()
                    if v:
                        resolved[fname] = FieldCandidate(v, conf, source, rule, prio)
                        logger.info("字段缺失补全（文件名）: %s=%s", fname, v)
                    break

        # 4) 组装输出（保持字段顺序）
        out: list[ExtractedField] = []
        for fname in _FIELD_ORDER:
            c = resolved.get(fname)
            if c:
                out.append(
                    ExtractedField(name=fname, value=c.value, confidence=c.confidence,
                                   source=c.source, rule=c.rule)
                )
        logger.info(
            "字段提取完成：%s%s",
            [f"{f.name}={f.value}" for f in out],
            f"（{len(self.last_conflicts)} 处冲突）" if self.last_conflicts else "",
        )
        return out


# 全局单例
field_extractor = FieldExtractor()
