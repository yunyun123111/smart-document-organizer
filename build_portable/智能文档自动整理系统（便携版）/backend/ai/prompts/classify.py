"""AI 分类 Prompt（规格书第二十五/二十六节）。

省 token 版：只要求返回 document_type / confidence / title / reason。
字段提取走本地正则（field_extractor），不再让 AI 输出 fields/keywords。
"""
from __future__ import annotations

SYSTEM_PROMPT = """你是一个专业的文档识别助手，负责判断文档类型。
你必须严格按以下 JSON 格式返回，不要输出任何额外文字：

{
  "document_type": "文档类型名称（必须来自给定的候选分类）",
  "confidence": 0.0到1.0之间的数字,
  "title": "简短文档标题（用于重命名）",
  "reason": "判断理由（一句话）"
}
"""


def build_classify_messages(
    text: str,
    candidate_types: list[str],
    known_fields: dict | None = None,
    rule_result: str | None = None,
) -> list[dict]:
    """构造分类请求消息（省 token：不携带字段明细）。"""
    rule_result = rule_result or "无"

    user_prompt = f"""请判断以下文档的类型。

候选文档类型（必须从中选择，如都不匹配则选"其他"）：
{chr(10).join('- ' + t for t in candidate_types)}

规则引擎判断结果：{rule_result}

文档文本：
---
{text}
---
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
