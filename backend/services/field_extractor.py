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
from backend.services.ocr_cleaner import ocr_cleaner

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
    r"(?:开票日期|签订日期|交易日期|交易时间|合同日期|记账日期|入账日期|回单日期|成交日期|日期|签署日期|出票日期)[:：]?\s*"
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

# 结算单结算额锚点：标签 + 任意个货币符号（OCR 会把 ¥ 重影成 ¥¥¥，实测 3 个）
_SETTLE_AMOUNT_ANCHOR = re.compile(
    r"(?:最终结算金额|结算总金额|最终结算价款|结算金额|结算总额|结算价款)"
    r"[:：]?\s*[¥￥]*\s*([\d,]+(?:\.\d{1,2})?)"
)

# 金额：带货币标识或"元"，支持千分位/小数
# 注意：OCR 文本里"金额"标签后面紧接的可能是发票号码（20 位）或银行流水号，
# 因此所有金额取值都要过 _plausible_amount 值域校验，不能只靠正则边界。
_AMOUNT_PATTERNS = [
    re.compile(
        r"(?:价税合计|含税合计|合计金额|含税金额|总金额)[\s\S]{0,40}?"
        r"(?:¥|￥|人民币|RMB)?\s*([\d,]+(?:\.\d{1,2})?)"
    ),
    re.compile(r"(?:人民币|RMB|¥|￥)\s*([\d,]+(?:\.\d{1,2})?)\s*(?:元)?"),
    re.compile(r"(?:金额|价款|货款|总价|交易金额)[:：]?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:元|人民币)?"),
    re.compile(r"([\d,]+(?:\.\d{1,2})?)\s*元"),
]


def _plausible_amount(raw: str | None) -> bool:
    """金额值域校验：排除发票号/税号/银行流水这类长整数串。

    含小数的一定是金额；纯整数且 ≥16 位视为单号（20 位数电票号、18 位税号、
    16 位以上流水号），不是金额。
    """
    s = (raw or "").replace(",", "").strip()
    if not s:
        return False
    return not ("." not in s and len(s) >= 16)

# ---------- 银行回单版式（新版式：标签与值分行，"金额（小写）\n1,500,000.00"） ----------
_RECEIPT_SMALL_AMOUNT = re.compile(
    r"金额\s*[（(]\s*小写\s*[）)]\s*[:：]?\s*[¥￥]?\s*([\d,]+(?:\.\d{1,2})?)"
)
_RECEIPT_BIG_AMOUNT = re.compile(
    r"金额\s*[（(]\s*大写\s*[）)]\s*[:：]?\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆元角分整]{3,40})"
)
# 回单款项性质/摘要（用于区分货款、保证金、利息、退款）
# 按优先级逐个匹配：摘要 > 款项性质 > 用途 > 附言 > 交易描述
_SUMMARY_CONTEXT = re.compile(
    r"(?:摘要|用途|交易描述|附言|款项性质|汇款用途)[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9，,。.\-（）()]{2,40}?)(?=\s*(?:\n|$))"
)
_SUMMARY_RULES = [
    ("摘要", re.compile(r"摘要[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9，,。.\-（）() ]{2,30}?)(?=\s*(?:\n|$))")),
    ("款项性质", re.compile(r"款项性质[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9，,。.\-（）() ]{2,30}?)(?=\s*(?:\n|$))")),
    ("用途", re.compile(r"(?:用途|汇款用途)[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9，,。.\-（）() ]{2,30}?)(?=\s*(?:\n|$))")),
    ("附言", re.compile(r"附言[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9，,。.\-（）() ]{2,30}?)(?=\s*(?:\n|$))")),
    ("交易描述", re.compile(r"交易描述[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9，,。.\-（）() ]{2,30}?)(?=\s*(?:\n|$))")),
]

# ---------- 电子发票版式（PDF 文本流为"标签块在前、值块在后"错位排列） ----------
# 数电票号码固定 20 位纯数字；税号为 18 位含字母，不会误命中
# OCR 常把长号码按 4 位一组断行，允许数字之间夹空白，取值时再去空白
_INVOICE_NO_ANCHOR = re.compile(r"(?<![0-9])((?:[0-9]\s*){20})(?![0-9])")
# 开票日期：2026年06月18日 / 2026 年 6 月 18 日
_INVOICE_DATE_ANCHOR = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# ¥ 前缀金额（价税合计 / 不含税金额 / 税额三类）
_INVOICE_YUAN_AMOUNT = re.compile(r"[¥￥]\s*([\d,]+\.\d{2})")
# 数值 token（明细区勾稽用）
_NUMBER_TOKEN = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+\.\d+|\d+(?:\.\d+)?)(?![\d.])")

# 船名：上下文锚定（结算单/货权转移单通常带"船名："，支持数字结尾如 海兴168）
_VESSEL_CONTEXT = re.compile(
    r"(?:船名|船舶名称|船号)[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9 （）()\-]{2,60}?)(?=[，,;；。]|\s*(?:\r?\n|$))"
)
# 引号内船名（如 "NIGHTSKY夜空"轮）
_VESSEL_QUOTED = re.compile(
    r"[“\"]([\u4e00-\u9fa5A-Za-z0-9\-/（）()]{2,40}?)[”\"]\s*(?:轮|号|船|货轮)"
)
# 物料名称：上下文锚定 + 前瞻截断
_MATERIAL_CONTEXT = re.compile(
    r"(?:物料名称|物料|品名|品种|货物名称|货物品名|商品名称)[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9（）()%\.\-]{2,40}?)(?=\s*(?:数量|单位|规格|单价|备注|吨|[，,;；]|$))"
)
# 吨数后的粉/矿/钢/煤/油类物料（如 10000吨PB粉、500O吨铁矿粉）
_MATERIAL_TON = re.compile(
    r"(?:吨|T|t)\s*([\u4e00-\u9fa5A-Za-z]{1,12}(?:粉|矿|钢|煤|油|砂))"
)
# 数量：上下文锚定 + 单位
# 小数位限制 2 位：OCR 常把"9914.99 707.15"粘成"9914.99707.15"，
# 若允许 3 位会吃成 9914.997
_QUANTITY_CONTEXT = re.compile(
    r"(?:数量|吨数|总数量|重量|净重)[:：]?[\s\S]{0,50}?([\d,]+(?:\.\d{1,2})?)\s*(?:吨|T|t|kg|KG|千克|件|个|张|份|批)?"
)

# 发票特征标记（用于触发发票专用提取）
_INVOICE_MARKER = re.compile(r"电子发票|增值税|价税合计|增值税专用发票")

# 销售方：发票/结算单中"销售方信息 名称：XXX公司"
_SELLER_CONTEXT = re.compile(
    r"(?:销售方|销方|卖方|供货方|供应商)(?:信息)?[\s\S]{0,50}?名称[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,40}?(?:有限公司|有限责任公司|股份有限公司|公司|集团|厂))"
)

# ---------- 文件名字段提取（缺失补全） ----------
# 合同号：SJWLXS（DD）-2026-YC0453 / SJWLCG（DD）-2026-0109 / SOTC202605130002
# 关键约束：编号中的 4 位数字必须是年份（19xx/20xx），否则纯哈希文件名
# （如 ef252262087373843227213824214.pdf / db5670f42.jpg）会被抽成垃圾合同号。
_FN_CONTRACT_NO = re.compile(
    r"([A-Z]{2,10}(?:（DD）|\(DD\)|[-_])?[-_/]?(?:19|20)\d{2}[-_/]?[A-Z]{0,6}\d{2,10})(?!\d)|"
    r"([A-Z]{2,6}(?:19|20)\d{2}\d{0,10})(?!\d)",
    re.IGNORECASE,
)
# 纯哈希文件名（16 位以上连续十六进制串）：不参与合同号兜底
_HASH_LIKE = re.compile(r"(?<![0-9a-z])[0-9a-f]{16,}(?![0-9a-z])", re.IGNORECASE)
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


# 页码/页眉噪声行：`1 / 10`、`第 1 页`、`- 2 -`、短纯数字行
# 注意：4 位以上纯数字可能是正文数据（如发票数量 7996），只在文档首尾行按页码处理
_PAGE_NOISE = re.compile(
    r"^\s*(?:第\s*\d+\s*页|页\s*\d+|[-–—]?\s*\d+\s*/\s*\d+\s*[-–—]?|\d+\s*/\s*\d+|\d{1,3}|[-–—]\s*\d+\s*[-–—])\s*$"
)
_PAGE_NOISE_EDGE = re.compile(r"^\s*\d{4,5}\s*$")
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
    src_lines = text.splitlines()
    n_lines = len(src_lines)
    lines = []
    for i, ln in enumerate(src_lines):
        st = ln.strip()
        if not st:
            continue
        if _PAGE_NOISE.match(st):
            continue
        # 4 位以上纯数字：正文里可能是数量/金额，只在文档首尾 3 行内按页码剔除
        if (i < 3 or i >= n_lines - 3) and _PAGE_NOISE_EDGE.match(st):
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
    """发票价税合计：取所有 ¥ 金额中的最大值。

    价税合计 = 不含税金额 + 税额，恒为票面 ¥ 金额的最大值；
    只认带 ¥ 前缀的值，避免把明细区单价/数量/税率当金额。
    """
    if not _INVOICE_MARKER.search(text):
        return None
    yuan: list[tuple[float, str]] = []
    for v in _INVOICE_YUAN_AMOUNT.findall(text):
        if not _plausible_amount(v):
            continue
        try:
            num = float(v.replace(",", ""))
        except ValueError:
            continue
        yuan.append((num, v.replace(",", "")))
    if yuan:
        return max(yuan)[1], 0.95, "invoice_amount_yuan", 96.0, SOURCE_RULE
    # 兜底：无 ¥ 前缀时取最大小数金额
    amounts: list[tuple[float, str]] = []
    for v in re.findall(r"\d[\d,]*\.\d{1,2}", text):
        if not _plausible_amount(v):
            continue
        try:
            num = float(v.replace(",", ""))
        except ValueError:
            continue
        amounts.append((num, v.replace(",", "")))
    if amounts:
        return max(amounts)[1], 0.85, "invoice_amount_max", 90.0, SOURCE_RULE
    return None


def _invoice_no_matcher(text: str):
    """发票号码：数电票固定 20 位纯数字（税号为 18 位含字母，不会误命中）。"""
    if not _INVOICE_MARKER.search(text):
        return None
    m = _INVOICE_NO_ANCHOR.search(text)
    if m:
        digits = re.sub(r"\s+", "", m.group(1))
        if len(digits) == 20:
            return digits, 0.95, "invoice_no_anchor", 98.0, SOURCE_RULE
    return None


def _invoice_date_matcher(text: str):
    """开票日期：票面第一处 YYYY年MM月DD日。"""
    if not _INVOICE_MARKER.search(text):
        return None
    m = _INVOICE_DATE_ANCHOR.search(text)
    if m:
        return _norm_date(m.group(1), m.group(2), m.group(3)), 0.95, "invoice_date", 96.0, SOURCE_RULE
    return None


def _invoice_quantity_matcher(text: str):
    """发票数量：明细区用「数量 × 单价 ≈ 金额」勾稽求解。

    发票文本流里数量、单价、金额、税额挤在一起且顺序不固定，
    按位置取数会把不含税金额或单价小数当数量。
    单价特征为小数位 >= 4，再用乘法勾稽锁定数量。
    """
    if not _INVOICE_MARKER.search(text):
        return None
    idx = text.find("吨")
    seg = text[idx: idx + 500] if idx >= 0 else text
    tokens = _NUMBER_TOKEN.findall(seg)
    if len(tokens) < 2:
        return None
    nums: list[float] = []
    for t in tokens:
        try:
            nums.append(float(t.replace(",", "")))
        except ValueError:
            continue
    price_tok = next((t for t in tokens if re.search(r"\.\d{4,}", t)), None)
    if not price_tok:
        return None
    try:
        price = float(price_tok.replace(",", ""))
    except ValueError:
        return None
    if price <= 0:
        return None
    # 单价本身不能当"金额"参与勾稽：否则 v=1 时 1×单价 ≈ 单价 会巧合命中，
    # 把发票数量抽成 1（下游发票实测踩过）。
    others = [a for a in nums if a > 0 and abs(a - price) > 1e-6]
    if not others:
        return None
    # 勾稽：对每个候选数量，找一个明细金额使 数量×单价 相对误差 < 1%
    best: tuple[float, str] | None = None
    for t in tokens:
        if t == price_tok:
            continue
        try:
            v = float(t.replace(",", ""))
        except ValueError:
            continue
        if v <= 0 or abs(v - price) < 1e-9:
            continue
        gaps = [abs(v * price - a) / a for a in others]
        if not gaps:
            continue
        gap = min(gaps)
        if gap >= 0.01:
            continue
        if best is None or gap < best[0]:
            best = (gap, t.replace(",", ""))
    if best:
        return best[1], 0.9, "invoice_quantity_math", 92.0, SOURCE_RULE
    return None


def _receipt_small_amount_matcher(text: str):
    """银行回单：标签与值分行的「金额（小写）」版式。"""
    m = _RECEIPT_SMALL_AMOUNT.search(text)
    if m:
        return m.group(1).replace(",", ""), 0.96, "receipt_amount_small", 97.0, SOURCE_RULE
    return None


def _receipt_summary_matcher(text: str):
    """回单摘要 / 用途 / 交易描述（供款项性质分类）。

    按优先级取：摘要（真正的款项性质）> 款项性质 > 用途 > 附言 > 交易描述。
    不能直接用最靠前的标签，回单里"交易描述"排在"摘要"前面，先命中会拿到
    "大额汇入"这类交易类型而非款项性质。
    """
    for _, pat in _SUMMARY_RULES:
        m = pat.search(text)
        if not m:
            continue
        v = m.group(1).strip().rstrip("，,。.")
        if v and v not in ("明细标注", "备注"):
            return v, 0.85, "receipt_summary", 88.0, SOURCE_RULE
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
        if m and _plausible_amount(m.group(1)):
            return _norm_amount(m.group(1)), conf, rule, prio, SOURCE_RULE
        return None
    return _m


def _settlement_amount_matcher(text: str):
    """结算单：优先认「最终结算金额 / 结算总金额」标签。

    结算单正文里同时有「最终结算金额 7,922,962.05」「已收货款 7,990,545.00」
    「应补货款 -67,582.95」多个金额，靠"第一个金额"或"最大值"都会抽错。
    扫描件还会写成「¥¥7,882,417.05」（OCR 把符号重影成两个），故前缀允许重复。
    """
    m = _SETTLE_AMOUNT_ANCHOR.search(text)
    if m and _plausible_amount(m.group(1)):
        return _norm_amount(m.group(1)), 0.97, "settlement_amount_anchor", 99.0, SOURCE_RULE
    return None


def _quantity_context_m(text: str):
    m = _QUANTITY_CONTEXT.search(text)
    if m:
        return _norm_amount(m.group(1)), 0.85, "quantity_context", 85.0, SOURCE_RULE
    return None

# 合同尾号：SJWLXS（DD）-2026-YC0458 取 YC 后 4 位；手写难识别，缺失时命名自动跳过
_CONTRACT_SUFFIX = re.compile(r"YC[-_\-]?\s*([A-Za-z0-9]{4})", re.IGNORECASE)


def _contract_suffix_m(text: str):
    m = _CONTRACT_SUFFIX.search(text)
    if m:
        return m.group(1).upper(), 0.65, "contract_suffix_yc", 78.0, SOURCE_RULE
    return None


def _clean_vessel(v: str) -> str:
    # 去掉中文括号别名（如 ZJE OCEAN 1(浙能海 1) -> ZJE OCEAN 1）与残留括号
    v = re.sub(r"[（(][^）)]*[）)]", "", v)
    v = v.strip(" ()）(")
    # 去空白，船名不保留空格，避免文件名含空格
    return re.sub(r"\s+", "", v)

def _vessel_context_m(text: str):
    m = _VESSEL_CONTEXT.search(text)
    if m:
        v = _clean_vessel(m.group(1))
        if v:
            return v, 0.85, "vessel_context", 85.0, SOURCE_RULE
    return None


def _vessel_quoted_m(text: str):
    m = _VESSEL_QUOTED.search(text)
    if m:
        v = _clean_vessel(m.group(1))
        if v:
            return v, 0.8, "vessel_quoted", 80.0, SOURCE_RULE
    return None


# 文件名专用 matcher（规范化输出 + 至少含一个汉字，避免数字误判）
def _filename_contract_matcher(stem: str):
    """文件名合同号：排除纯哈希文件名，避免把哈希串当编号。"""
    if _HASH_LIKE.search(stem):
        return None
    m = _FN_CONTRACT_NO.search(stem)
    if not m:
        return None
    for g in range(1, len(m.groups()) + 1):
        v = m.group(g)
        if v:
            # 统一全角括号，去掉多余空白
            return re.sub(r"\s+", "", v).upper(), 0.6, "filename_contract", 60.0, SOURCE_FILENAME
    return None


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
        _invoice_date_matcher,
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
        _invoice_no_matcher,
        _m_group(_INVOICE_NO, 1, 0.85, "invoice_context", 100.0),
    ],
    "amount": [
        # 结算单专用锚点放最前：正文里同时有结算额、已收/已付货款、应补差额，
        # 通用金额规则会先抽到"已收货款"或差额
        _settlement_amount_matcher,
        _invoice_amount_matcher,
        _receipt_small_amount_matcher,
        _amount_matcher(_AMOUNT_PATTERNS[0], 0.95, "amount_total", 95.0),
        _amount_matcher(_AMOUNT_PATTERNS[1], 0.9, "amount_currency", 90.0),
        _amount_matcher(_AMOUNT_PATTERNS[2], 0.75, "amount_context", 75.0),
        _amount_matcher(_AMOUNT_PATTERNS[3], 0.6, "amount_yuan", 60.0),
    ],
    "summary": [
        _receipt_summary_matcher,
    ],
    "vessel": [
        _vessel_context_m,
        _vessel_quoted_m,
    ],
    "material": [
        _m_group(_MATERIAL_CONTEXT, 1, 0.8, "material_context", 80.0),
        _m_group(_MATERIAL_TON, 1, 0.7, "material_ton", 70.0),
    ],
    "quantity": [
        _invoice_quantity_matcher,
        _quantity_context_m,
    ],
    "contract_suffix": [
        _contract_suffix_m,
    ],
}

# 文件名补全字段：缺失时从文件名提取（低优先级，source=FILENAME）
_FILENAME_RULES: dict[str, list] = {
    "contract_no": [
        _filename_contract_matcher,
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
_FIELD_ORDER = ["company", "seller", "date", "contract_no", "contract_suffix", "order_no", "invoice_no",
                "amount", "summary", "vessel", "material", "quantity"]


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
        known_companies: list[str] | None = None,
    ) -> list[ExtractedField]:
        """从清洗后的文本中提取字段（可附文件名做缺失补全）。

        known_companies: 已知公司名单（OCR 公司名模糊匹配用）。
        """
        self.last_conflicts = []
        # 基础清洗（竖排/页码/水印）→ OCR 二次清洗（数字纠错/日期统一/公司名/发票代码）
        text = clean_ocr_text(text or "")
        text = ocr_cleaner.clean(text, known_companies=known_companies).text
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
            self._fill_from_filename(str(filename).strip(), resolved)

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

    # ---------- 文件名级抽取（零解析成本） ----------
    def _fill_from_filename(self, stem: str, resolved: dict[str, FieldCandidate]) -> None:
        """用文件名补齐缺失字段（只填尚未解析出的字段）。"""
        if not stem:
            return
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

    def extract_filename_only(self, filename: str) -> list[ExtractedField]:
        """只跑文件名规则（不解析内容），供文件名规则命中的快速通道补字段。"""
        self.last_conflicts = []
        resolved: dict[str, FieldCandidate] = {}
        self._fill_from_filename(str(filename or "").strip(), resolved)
        return [
            ExtractedField(name=fname, value=c.value, confidence=c.confidence,
                           source=c.source, rule=c.rule)
            for fname in _FIELD_ORDER
            if (c := resolved.get(fname))
        ]


# 全局单例
field_extractor = FieldExtractor()
