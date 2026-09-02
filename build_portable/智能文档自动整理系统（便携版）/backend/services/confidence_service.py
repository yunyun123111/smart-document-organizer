"""置信度系统（规格书第二十八节）。

最终置信度 = 规则×40% + 字段×20% + 关键词×15% + AI×25%（权重可配置）。
某个来源不可用（如 AI 未启用）时，把该部分权重归一化到其余来源，
避免因来源缺失导致置信度系统性偏低。

分档：
- >= auto_archive_threshold (0.85) → 自动归档
- >= review_threshold (0.60)     → 人工确认
- <  review_threshold            → 无法判断
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger("services.confidence_service")

# 决策结果
DECISION_AUTO = "auto"
DECISION_REVIEW = "review"
DECISION_REJECT = "reject"


@dataclass
class ConfidenceResult:
    """置信度计算结果。"""
    score: float
    decision: str
    components: dict  # 各来源置信度

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "decision": self.decision,
            "components": self.components,
        }


class ConfidenceService:
    """置信度服务。

    阈值与权重默认**实时**读取 settings，因此在系统设置页改完立即生效，
    无需重启进程；构造时显式传入的值则作为固定覆盖（供测试/特殊场景）。
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        auto_archive_threshold: float | None = None,
        review_threshold: float | None = None,
    ):
        self._weights = {k: float(v) for k, v in weights.items()} if weights else None
        self._auto_archive_threshold = auto_archive_threshold
        self._review_threshold = review_threshold

    @property
    def weights(self) -> dict[str, float]:
        if self._weights is not None:
            return self._weights
        return settings.confidence_weights()

    @property
    def auto_archive_threshold(self) -> float:
        if self._auto_archive_threshold is not None:
            return self._auto_archive_threshold
        return settings.AUTO_ARCHIVE_THRESHOLD

    @property
    def review_threshold(self) -> float:
        if self._review_threshold is not None:
            return self._review_threshold
        return settings.REVIEW_THRESHOLD

    def calculate(
        self,
        rule_conf: float | None = None,
        field_conf: float | None = None,
        keyword_conf: float | None = None,
        ai_conf: float | None = None,
    ) -> ConfidenceResult:
        """计算综合置信度并分档。None 表示该来源不可用。"""
        sources = {
            "rule": _clamp(rule_conf),
            "field": _clamp(field_conf),
            "keyword": _clamp(keyword_conf),
            "ai": _clamp(ai_conf),
        }
        available = {k: v for k, v in sources.items() if v is not None}
        weights = self.weights
        total_weight = sum(weights.get(k, 0.0) for k in available)
        if total_weight <= 0:
            return ConfidenceResult(score=0.0, decision=DECISION_REJECT, components=sources)

        score = sum(available[k] * weights.get(k, 0.0) for k in available) / total_weight
        decision = self.decide(score)
        return ConfidenceResult(score=round(score, 4), decision=decision, components=sources)

    def decide(self, confidence: float) -> str:
        if confidence >= self.auto_archive_threshold:
            return DECISION_AUTO
        if confidence >= self.review_threshold:
            return DECISION_REVIEW
        return DECISION_REJECT


def _clamp(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(1.0, float(v)))


# 全局单例
confidence_service = ConfidenceService()
