"""Phase 8 置信度系统测试：加权计算/归一化/阈值分档/可配置。"""
from __future__ import annotations

import pytest

from backend.services.confidence_service import (
    DECISION_AUTO,
    DECISION_REJECT,
    DECISION_REVIEW,
    ConfidenceService,
)


class TestWeightedCalculation:
    def test_default_weights(self):
        # 规则0.4 字段0.2 关键词0.15 AI0.25
        svc = ConfidenceService()
        r = svc.calculate(rule_conf=1.0, field_conf=0.5, keyword_conf=0.5, ai_conf=0.8)
        expected = (1.0 * 0.4 + 0.5 * 0.2 + 0.5 * 0.15 + 0.8 * 0.25) / 1.0
        assert r.score == pytest.approx(expected)

    def test_custom_weights(self):
        svc = ConfidenceService(weights={"rule": 0.5, "field": 0.5, "keyword": 0.0, "ai": 0.0})
        r = svc.calculate(rule_conf=1.0, field_conf=0.0)
        assert r.score == pytest.approx(0.5)  # (1*0.5 + 0*0.5)/1.0

    def test_normalize_when_source_missing(self):
        # AI 不可用时（None），其余权重归一化
        svc = ConfidenceService()
        r = svc.calculate(rule_conf=1.0, field_conf=1.0, keyword_conf=1.0, ai_conf=None)
        # 可用权重 = 0.4+0.2+0.15 = 0.75，分数 = (1*0.4+1*0.2+1*0.15)/0.75 = 1.0
        assert r.score == pytest.approx(1.0)

    def test_all_none_returns_reject(self):
        svc = ConfidenceService()
        r = svc.calculate()
        assert r.score == 0.0
        assert r.decision == DECISION_REJECT


class TestThresholdDecision:
    def test_auto_archive(self):
        svc = ConfidenceService()
        assert svc.decide(0.90) == DECISION_AUTO
        assert svc.decide(0.85) == DECISION_AUTO  # 边界

    def test_review(self):
        svc = ConfidenceService()
        assert svc.decide(0.84) == DECISION_REVIEW
        assert svc.decide(0.60) == DECISION_REVIEW  # 边界

    def test_reject(self):
        svc = ConfidenceService()
        assert svc.decide(0.59) == DECISION_REJECT
        assert svc.decide(0.0) == DECISION_REJECT

    def test_custom_thresholds(self):
        svc = ConfidenceService(auto_archive_threshold=0.9, review_threshold=0.7)
        assert svc.decide(0.9) == DECISION_AUTO
        assert svc.decide(0.75) == DECISION_REVIEW
        assert svc.decide(0.65) == DECISION_REJECT

    def test_thresholds_follow_live_settings(self):
        """单例必须实时读取配置：系统设置页改阈值后立即生效，无需重启。"""
        from backend.config import settings

        svc = ConfidenceService()  # 模块级单例在导入期就已创建
        original = settings.AUTO_ARCHIVE_THRESHOLD
        try:
            settings.AUTO_ARCHIVE_THRESHOLD = 0.99
            assert svc.decide(0.95) == DECISION_REVIEW, "阈值应实时反映配置变更"
            settings.AUTO_ARCHIVE_THRESHOLD = 0.5
            assert svc.decide(0.55) == DECISION_AUTO
        finally:
            settings.AUTO_ARCHIVE_THRESHOLD = original

    def test_weights_follow_live_settings(self):
        from backend.config import settings

        svc = ConfidenceService()
        original = settings.CONF_AI_WEIGHT
        try:
            # AI 权重调高后，同一输入的综合置信度应随之变化
            settings.CONF_AI_WEIGHT = 0.25
            low = svc.calculate(rule_conf=0.5, ai_conf=1.0).score
            settings.CONF_AI_WEIGHT = 0.9
            high = svc.calculate(rule_conf=0.5, ai_conf=1.0).score
            assert high > low
        finally:
            settings.CONF_AI_WEIGHT = original


class TestClamping:
    def test_clamp_values(self):
        svc = ConfidenceService()
        r = svc.calculate(rule_conf=1.5, ai_conf=-0.2)
        # 越界值被钳制到 0-1
        assert all(0 <= v <= 1 for v in r.components.values() if v is not None)
