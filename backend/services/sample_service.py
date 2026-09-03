"""文档格式样本学习与版式匹配服务（人工样例 -> 自动归档）。

核心思想：用户上传清晰样本，系统本地提取"版式指纹"，后续同版式文档零 AI 命中归档。

指纹三维（全部纯本地计算，零 token、零外部依赖）：
1. labels   —— 业务标签词命中统计（合同号/船名/日期/数量…）
2. grams    —— 相邻中文 2-gram 词频（捕捉业务特有词：铁矿粉、湿吨、车板交货…）
3. fields   —— 字段提取器能从样本提取到的字段名清单（锚点）

匹配评分：
    score = 0.35*标签词覆盖率 + 0.35*2-gram 覆盖率 + 0.3*字段锚点命中率
命中阈值默认 0.68（可调低更激进、调高更保守）。
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy.orm import Session

from backend.models import DocumentSample
from backend.services.field_extractor import field_extractor
from backend.utils.logger import get_logger

logger = get_logger("services.sample_service")

# 命中阈值：>= 此分视为"同版式"，自动归档
SAMPLE_MATCH_THRESHOLD = 0.68

# 业务标签词（版式识别高区分度词）
LABEL_WORDS: list[str] = [
    "合同号", "合同编号", "编号", "船名", "船舶", "日期", "数量", "单价",
    "金额", "总金额", "税率", "税额", "发票号码", "发票号", "开票日期",
    "供应商", "采购方", "采购合同", "销售合同", "需方", "供方", "买方", "卖方",
    "货物名称", "规格", "产地", "交货", "结算单", "结算", "货权", "验收",
    "质量", "付款", "收款", "开户行", "账号", "大写", "小写", "货物",
    "订单", "报价", "发货", "到货", "湿吨", "吨位", "备注", "签约地",
    "违约责任", "货物明细", "化学成分", "含量",
]

# 无区分度停用词（匹配时忽略）
_STOP_GRAM_CHARS = set("的了是在和有就都而及与或一个也不")

# 用于生成 2-gram 的中文字符
_CN_RE = re.compile(r"[\u4e00-\u9fff]")
_LABEL_RE = None


def _contains(word: str, text: str) -> bool:
    return word in text


def build_fingerprint(text: str) -> dict:
    """从清洗后的文档文本提取版式指纹。"""
    text = (text or "").strip()
    stats = {
        "chars": len(text),
        "lines": len([l for l in text.splitlines() if l.strip()]),
        "digit_ratio": round(
            len(re.findall(r"[0-9]", text)) / max(1, len(re.sub(r"\s", "", text))), 3
        ),
    }

    # 1) 标签词命中
    labels: dict[str, int] = {}
    for w in LABEL_WORDS:
        if w in text:
            labels[w] = text.count(w)

    # 2) 相邻中文 2-gram 频率
    grams: dict[str, int] = Counter()
    chars = _CN_RE.findall(text)
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in _STOP_GRAM_CHARS or b in _STOP_GRAM_CHARS:
            continue
        grams[a + b] += 1
    # 只保留有区分度的高频 gram（出现至少 2 次，且去掉纯停用字组合）
    top_grams = {g: c for g, c in grams.items() if c >= 2}
    # 截断到最多 200 个，控制指纹体积
    top_grams = dict(sorted(top_grams.items(), key=lambda kv: -kv[1])[:200])

    # 3) 字段锚点（样本可提取到的字段名）
    fields = sorted({f.name for f in field_extractor.extract(text)})

    return {"labels": labels, "grams": top_grams, "fields": fields, "stats": stats}


def match_score(fingerprint: dict, text: str, extracted_field_names: list[str]) -> float:
    """计算文档文本与样本指纹的匹配分（0~1）。"""
    text = text or ""
    if not text:
        return 0.0

    # 1) 标签词覆盖率（加权）
    labels = fingerprint.get("labels") or {}
    total_w = sum(labels.values())
    label_score = 0.0
    if total_w:
        hit = sum(c for w, c in labels.items() if _contains(w, text))
        label_score = hit / total_w

    # 2) 2-gram 覆盖率（加权）
    grams = fingerprint.get("grams") or {}
    total_g = sum(grams.values())
    gram_score = 0.0
    if total_g:
        hit_g = sum(c for g, c in grams.items() if g in text)
        gram_score = hit_g / total_g

    # 3) 字段锚点命中率
    fields = fingerprint.get("fields") or []
    anchor_score = 0.0
    if fields:
        hit_f = sum(1 for f in fields if f in extracted_field_names)
        anchor_score = hit_f / len(fields)

    score = 0.35 * label_score + 0.35 * gram_score + 0.3 * anchor_score
    return round(score, 4)


class SampleService:
    """样本学习 + 版式匹配的对外入口。"""

    def match_all(
        self, db: Session, text: str, extracted_field_names: list[str]
    ) -> tuple[DocumentSample | None, float]:
        """在所有启用样本中找最佳匹配；分数达标返回样本。"""
        if not text:
            return None, 0.0
        samples = (
            db.query(DocumentSample)
            .filter(DocumentSample.enabled.is_(True))
            .all()
        )
        best: DocumentSample | None = None
        best_score = 0.0
        for s in samples:
            score = match_score(s.fingerprint_dict, text, extracted_field_names)
            if score > best_score:
                best_score = score
                best = s
        if best is not None and best_score >= SAMPLE_MATCH_THRESHOLD:
            return best, best_score
        return None, best_score


sample_service = SampleService()
