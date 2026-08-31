"""AI 字段提取 Prompt（规格书第二十六节）。

根据文档类型提取关键字段，返回严格 JSON。
"""
from __future__ import annotations

SYSTEM_PROMPT = """你是文档字段提取助手。根据文档类型从文本中提取关键字段。
必须严格按 JSON 返回，格式：
{
  "fields": { "字段key": "字段值" },
  "confidence": 0.0到1.0,
  "reason": "一句话说明"
}
字段值必须是文本中的原样内容；找不到的字段不要编造，直接省略。
"""

_FIELD_HINTS = {
    "合同": ["contract_no", "company", "date", "amount"],
    "发票": ["invoice_no", "company", "date", "amount", "tax_rate"],
    "银行回单": ["payer", "payee", "date", "amount"],
    "对账单": ["company", "period", "amount"],
    "订单": ["order_no", "company", "date", "amount"],
    "报价单": ["company", "date", "amount"],
}


def build_extract_messages(text: str, document_type: str | None = None) -> list[dict]:
    doc_type = document_type or "未知"
    hints = ""
    for key, vals in _FIELD_HINTS.items():
        if key in doc_type:
            hints = "优先提取字段：" + ", ".join(vals)
            break

    user_prompt = f"""文档类型：{doc_type}
{hints}

文档文本：
---
{text}
---
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
