"""字段提取器（规格书 Phase 6 / 第二十三节）。

先使用规则与正则实现以下字段：
- company（公司）
- date（日期）
- contract_no（合同编号）
- order_no（订单编号）
- invoice_no（发票号码）
- amount（金额）

策略：优先"上下文锚定"模式（如"合同编号："），再退化为通用模式。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.utils.logger import get_logger

logger = get_logger("services.field_extractor")

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
# 裸"合同"前缀（如"双方合同SJWLXS（DD）-2026-YC0433下"），
# 负向前瞻排除 单价/数量/金额 等非编号语境
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
# 引号内船名（如 "NIGHTSKY夜空"轮、"HEBEIDYNASTY-河北王朝"轮）
_VESSEL_QUOTED = re.compile(
    r"[“\"]([\u4e00-\u9fa5A-Za-z0-9\-/（）()]{2,40}?)[”\"]\s*(?:轮|号|船|货轮)"
)
# 物料名称：上下文锚定 + 前瞻截断（到数量/单位/标点/行尾）
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

def _despaghettify(text: str) -> str:
    """合并 OCR 竖排产生的连续单字行（如 购/买/方/信/息 -> 购买方信息）。

    只合并每行恰好 1 个中文字符的连续行，双字及以上不合并，避免误伤正文。
    """
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


# 中文数字（用于金额金额转数字）
_CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass
class ExtractedField:
    """一个提取到的字段。"""
    name: str
    value: str
    confidence: float
    matched: list[str] = field(default_factory=list)  # 命中的原始文本片段

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "confidence": self.confidence,
            "source": "RULE",
        }


def _norm_date(y: str, m: str, d: str) -> str:
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _norm_amount(raw: str) -> str:
    """金额去千分位/货币符号，保留小数。"""
    digits = raw.replace(",", "")
    return digits


class FieldExtractor:
    """基于规则与正则的字段提取器。"""

    def extract(self, text: str, document_type: str | None = None) -> list[ExtractedField]:
        """从清洗后的文本中提取字段。"""
        text = _despaghettify(text or "")
        fields: list[ExtractedField] = []

        company = self._extract_company(text)
        if company:
            fields.append(company)

        seller = self._extract_seller(text)
        if seller:
            fields.append(seller)

        date = self._extract_date(text)
        if date:
            fields.append(date)

        for name, pattern in (
            ("contract_no", _CONTRACT_NO),
            ("order_no", _ORDER_NO),
            ("invoice_no", _INVOICE_NO),
        ):
            f = self._extract_by_pattern(name, pattern, text)
            if f:
                fields.append(f)

        # 裸"合同"前缀兜底（如"双方合同SJWLXS（DD）-2026-YC0433下"）
        if not any(f.name == "contract_no" for f in fields):
            f = self._extract_by_pattern("contract_no", _CONTRACT_NO_BARE, text)
            if f:
                fields.append(f)

        amount = self._extract_amount(text)
        if amount:
            fields.append(amount)

        vessel = self._extract_vessel(text)
        if vessel:
            fields.append(vessel)
        # 引号船名兜底
        if not any(f.name == "vessel" for f in fields):
            f = self._extract_by_pattern("vessel", _VESSEL_QUOTED, text)
            if f:
                fields.append(f)

        material = self._extract_material(text)
        if material:
            fields.append(material)
        # 吨后物料兜底（PB粉/铁矿粉等）
        if not any(f.name == "material" for f in fields):
            m = _MATERIAL_TON.search(text)
            if m:
                fields.append(
                    ExtractedField(
                        name="material", value=m.group(1).strip(),
                        confidence=0.7, matched=[m.group(0)],
                    )
                )

        quantity = self._extract_quantity(text)
        if quantity:
            fields.append(quantity)

        # 发票专用提取：文本含发票特征时，用稳定的尾部结构覆盖/补充关键字段
        if _INVOICE_MARKER.search(text):
            inv = self._extract_invoice_fields(text)
            inv_map = {f.name: f for f in inv}
            for name in ("seller", "quantity", "amount"):
                if name in inv_map:
                    fields = [f for f in fields if f.name != name]
                    fields.append(inv_map[name])

        logger.info("字段提取完成：%s", [f"{f.name}={f.value}" for f in fields])
        return fields

    # ---- 各字段实现 ----
    def _extract_company(self, text: str) -> ExtractedField | None:
        # 优先上下文（甲方/乙方/供应商等）
        for m in _COMPANY_CONTEXT.finditer(text):
            name = m.group(1).strip()
            if len(name) >= 2:
                return ExtractedField(
                    name="company", value=name, confidence=0.9, matched=[m.group(0)]
                )
        # 通用公司名（取第一个）
        m = _COMPANY_GENERIC.search(text)
        if m:
            return ExtractedField(
                name="company", value=m.group(1), confidence=0.7, matched=[m.group(0)]
            )
        return None

    def _extract_date(self, text: str) -> ExtractedField | None:
        m = _DATE_CONTEXT.search(text)
        if m:
            return ExtractedField(
                name="date",
                value=_norm_date(m.group(1), m.group(2), m.group(3)),
                confidence=0.95,
                matched=[m.group(0)],
            )
        m = _DATE_GENERIC.search(text)
        if m:
            return ExtractedField(
                name="date",
                value=_norm_date(m.group(1), m.group(2), m.group(3)),
                confidence=0.8,
                matched=[m.group(0)],
            )
        return None

    def _extract_by_pattern(self, name: str, pattern: re.Pattern, text: str) -> ExtractedField | None:
        m = pattern.search(text)
        if m:
            return ExtractedField(
                name=name, value=m.group(1), confidence=0.85, matched=[m.group(0)]
            )
        return None

    def _extract_amount(self, text: str) -> ExtractedField | None:
        # 依次尝试：货币标识 → 金额上下文 → 纯数字+元
        for pattern, conf in ((_AMOUNT_PATTERNS[0], 0.95), (_AMOUNT_PATTERNS[1], 0.9), (_AMOUNT_PATTERNS[2], 0.75)):
            m = pattern.search(text)
            if m:
                return ExtractedField(
                    name="amount",
                    value=_norm_amount(m.group(1)),
                    confidence=conf,
                    matched=[m.group(0)],
                )
        return None

    def _extract_vessel(self, text: str) -> ExtractedField | None:
        m = _VESSEL_CONTEXT.search(text)
        if m:
            return ExtractedField(
                name="vessel", value=m.group(1), confidence=0.85, matched=[m.group(0)]
            )
        return None

    def _extract_material(self, text: str) -> ExtractedField | None:
        m = _MATERIAL_CONTEXT.search(text)
        if m:
            return ExtractedField(
                name="material", value=m.group(1).strip(), confidence=0.8, matched=[m.group(0)]
            )
        return None

    def _extract_invoice_fields(self, text: str) -> list[ExtractedField]:
        """发票专用提取：基于增值税发票 OCR 的稳定尾部结构。

        尾部固定：物料名 -> 税率 -> 单位(吨) -> 金额 -> 税额 -> 单价 -> 数量
        公司名序列：购买方在前、销售方在后
        """
        out: list[ExtractedField] = []

        # 1) 销售方 = 公司名序列最后一个（购买方在前销售方在后）
        names: list[str] = []
        for m in _COMPANY_CONTEXT.finditer(text):
            nm = m.group(1).strip()
            if len(nm) >= 4 and "公司" in nm:
                names.append(nm)
        if not names:
            for m in _COMPANY_GENERIC.finditer(text):
                nm = m.group(1).strip()
                if len(nm) >= 4 and "公司" in nm:
                    names.append(nm)
        if names:
            out.append(ExtractedField("seller", names[-1], 0.85, [names[-1]]))

        # 2) 数量 = "吨"之后的独立小数数值最后一个
        ton_idx = text.rfind("吨")
        if ton_idx >= 0:
            tail = text[ton_idx:]
            decs = re.findall(r"(\d[\d,]*\.\d{1,4})", tail)
            if decs:
                qty = decs[-1].replace(",", "")
                out.append(ExtractedField("quantity", qty, 0.8, [decs[-1]]))

        # 3) 价税合计金额 = 文本中最大的带小数金额
        amounts: list[tuple[float, str]] = []
        for v in re.findall(r"\d[\d,]*\.\d{1,2}", text):
            num = float(v.replace(",", ""))
            amounts.append((num, v.replace(",", "")))
        if amounts:
            mx = max(amounts, key=lambda x: x[0])
            out.append(ExtractedField("amount", mx[1], 0.9, [mx[1]]))

        return out

    def _extract_seller(self, text: str) -> ExtractedField | None:
        """提取销售方名称（发票/结算单中销售方信息块）。"""
        m = _SELLER_CONTEXT.search(text)
        if m:
            return ExtractedField(
                name="seller", value=m.group(1).strip(), confidence=0.9, matched=[m.group(0)]
            )
        return None

    def _extract_quantity(self, text: str) -> ExtractedField | None:
        m = _QUANTITY_CONTEXT.search(text)
        if m:
            return ExtractedField(
                name="quantity",
                value=_norm_amount(m.group(1)),
                confidence=0.85,
                matched=[m.group(0)],
            )
        return None


# 全局单例
field_extractor = FieldExtractor()
