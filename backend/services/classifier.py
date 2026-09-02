"""分类引擎 / 识别编排服务（规格书第八节架构：分类引擎 → 字段提取 → 置信度计算）。

把完整识别链路串起来：
  解析 → (OCR) → 文本清洗 → 规则引擎 → 字段提取 → AI 兜底 → 综合置信度 → 建议分类+文件名

规则优先，AI 只兜底；不确定的结果（review/reject）交由人工审核。
业务代码（批量整理 / 人工审核 / 预览）统一调用本服务。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Category, RecognitionTemplate, RenameTemplate
from backend.services.ai_service import ai_service
from backend.services.confidence_service import (
    DECISION_AUTO,
    DECISION_REJECT,
    DECISION_REVIEW,
    ConfidenceResult,
    confidence_service,
)
from backend.services.field_extractor import field_extractor
from backend.services.filename_rule_service import filename_rule_service
from backend.services.ocr_service import ocr_service
from backend.services.parser_service import parser_service
from backend.services.rename_service import DEFAULT_TEMPLATE, rename_service
from backend.services.rule_engine import (
    RuleEngine,
    RuleMatch,
    rule_confidence,
)
from backend.services.text_service import clean_parsed_document
from backend.utils.logger import get_logger

logger = get_logger("services.classifier")


@dataclass
class AnalysisResult:
    """一次完整识别的结果（供归档/预览/人工审核使用）。"""
    file_path: str
    text: str = ""
    document_type: str = ""
    title: str = ""
    fields: dict = field(default_factory=dict)          # 字段名 -> (值, 置信度, 来源)
    field_values: dict = field(default_factory=dict)   # 字段名 -> 值（纯值，供重命名）
    rule_matches: list = field(default_factory=list)
    ai_result: dict | None = None
    field_conflicts: list = field(default_factory=list)  # 字段冲突记录
    confidence: object | None = None                    # ConfidenceResult
    decision: str = ""
    suggested_category: str = ""                        # 分类 path
    suggested_filename: str = ""
    needs_ocr: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        conf = self.confidence.to_dict() if self.confidence else None
        return {
            "file_path": self.file_path,
            "text": self.text[:2000],
            "document_type": self.document_type,
            "title": self.title,
            "fields": self.field_values,
            "field_conflicts": self.field_conflicts,
            "decision": self.decision,
            "confidence": conf,
            "suggested_category": self.suggested_category,
            "suggested_filename": self.suggested_filename,
            "needs_ocr": self.needs_ocr,
            "error": self.error,
        }


class ClassifierService:
    """识别编排：解析 + OCR + 规则 + 字段 + AI + 置信度。"""

    def __init__(self, db: Session):
        self.db = db
        self.rule_engine = RuleEngine(db)

    # ---------- 主入口 ----------
    def analyze(self, file_path: str | Path, document_id: int | None = None) -> AnalysisResult:
        path = Path(file_path)
        result = AnalysisResult(file_path=str(path))

        # 0. 文件名规则优先：命中直接归档，不解析内容（省 token 快速通道）
        fn_match = filename_rule_service.match(path.name, self.db)
        if fn_match:
            result.document_type = fn_match.category_name
            result.title = path.stem
            result.suggested_category = fn_match.category_path
            result.suggested_filename = path.name  # 保持原名归档
            result.confidence = ConfidenceResult(
                score=0.99, decision=DECISION_AUTO, components={"filename": 1.0}
            )
            result.decision = DECISION_AUTO
            logger.info("文件名规则命中 %s -> %s（直接归档）", path.name, fn_match.category_path)
            return result

        try:
            # 1. 解析
            parsed = parser_service.parse_file(path)
            parsed = clean_parsed_document(parsed)

            # 2. OCR 补充（扫描 PDF / 图片）
            if parsed.needs_ocr:
                result.needs_ocr = True
                parsed.text = self._ocr_text(path, parsed)

            text = parsed.text or ""
            result.text = text
            if not text:
                result.error = "未提取到任何文本内容"
                result.decision = DECISION_REJECT
                return result

            # 3. 规则引擎（优先）
            matches = self.rule_engine.evaluate(text)
            result.rule_matches = [m.__dict__ for m in matches]
            best: RuleMatch | None = matches[0] if matches else None
            rule_conf = rule_confidence(best) if best else 0.0

            # 4. 字段提取（多规则并行引擎：正则+关键词+模板+文件名补全）
            extracted = field_extractor.extract(text, filename=path.name)
            result.field_conflicts = [c.__dict__ for c in field_extractor.last_conflicts]
            fields_dict: dict[str, tuple] = {}
            for f in extracted:
                fields_dict[f.name] = (f.value, f.confidence, f.source or "RULE")
            result.fields = fields_dict
            result.field_values = {k: v[0] for k, v in fields_dict.items()}
            field_conf = (
                sum(f[1] for f in fields_dict.values()) / len(fields_dict)
                if fields_dict
                else 0.0
            )

            # 4.5 识别模板匹配（同类文件"记性"）：命中且关键字段齐全 -> 直接自动归档，跳过 AI
            tpl = self._match_template(best.category_name if best else None, fields_dict)
            if tpl is not None:
                result.document_type = best.category_name or tpl.document_type
                result.suggested_category = tpl.category_path
                result.confidence = ConfidenceResult(
                    score=0.96, decision=DECISION_AUTO, components={"template": 1.0}
                )
                result.decision = DECISION_AUTO
                render_fields = dict(result.field_values)
                if result.document_type and "document_type" not in render_fields:
                    render_fields["document_type"] = result.document_type
                template = self._category_template(tpl.category_path)
                result.suggested_filename = rename_service.render(
                    template, render_fields, Path(result.file_path).suffix
                )
                tpl.usage_count += 1
                self.db.commit()
                logger.info(
                    "识别模板命中 %s -> %s（自动归档，跳过 AI）",
                    path.name, tpl.category_path,
                )
                return result

            # 5. AI 兜底（可选）
            ai_conf = None
            ai_result = None
            candidate_types = self._candidate_types(best)
            ai_needed = settings.AI_ENABLED and (
                best is None or rule_conf < settings.AUTO_ARCHIVE_THRESHOLD
            )
            if ai_needed:
                ai_result = ai_service.classify_document(
                    text,
                    context={
                        "candidate_types": candidate_types,
                        "known_fields": result.field_values,
                        "rule_result": best.category_name if best else "无",
                    },
                )
                if ai_result:
                    # AI 置信度过低（如 OCR 模型返回 0.0）视为不可用，
                    # 避免拉低综合置信度、也不让低质结果覆盖规则。
                    ai_conf_raw = float(ai_result.get("confidence") or 0.0)
                    if ai_conf_raw >= 0.5:
                        ai_conf = ai_conf_raw
                    else:
                        ai_result = None

            # 6. 综合置信度
            keyword_conf = self._keyword_conf(best)
            conf = confidence_service.calculate(
                rule_conf=rule_conf,
                field_conf=field_conf,
                keyword_conf=keyword_conf,
                ai_conf=ai_conf,
            )
            result.confidence = conf
            result.decision = conf.decision

            # 7. 建议分类 + 文档类型
            self._set_suggestion(result, best, ai_result, text)

            logger.info(
                "识别完成 %s: 类型=%s 置信度=%.3f 决策=%s",
                path.name,
                result.document_type,
                conf.score,
                conf.decision,
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("识别失败 %s: %s", path, e)
            result.error = str(e)
            result.decision = DECISION_REJECT
            return result

    # ---------- 内部方法 ----------
    def _ocr_text(self, path: Path, parsed) -> str:
        """对需 OCR 的文件执行 OCR，返回拼接文本。"""
        file_type = parsed.metadata.get("file_type", "")
        if file_type == "pdf":
            pages = ocr_service.recognize_pdf(path)
            return "\n".join(p.text for p in pages if p.text)
        # 图片
        res = ocr_service.recognize_image_file(path)
        return res.text

    def _match_template(self, doc_type: str | None, fields: dict) -> "RecognitionTemplate | None":
        """识别模板匹配：同类型且关键字段齐全即命中。"""
        if not doc_type or doc_type in ("其他", "未识别"):
            return None
        tpl = (
            self.db.query(RecognitionTemplate)
            .filter(
                RecognitionTemplate.document_type == doc_type,
                RecognitionTemplate.enabled.is_(True),
            )
            .first()
        )
        if tpl is None:
            return None
        require = tpl.require_fields_list
        if not require:
            return None
        if all(fields.get(k) for k in require):
            return tpl
        return None

    def _candidate_types(self, best: RuleMatch | None) -> list[str]:
        """生成 AI 候选文档类型列表。"""
        names = []
        for c in (
            self.db.query(Category)
            .filter(Category.enabled.is_(True))
            .order_by(Category.sort_order)
            .all()
        ):
            names.append(c.name)
        if best and best.category_name not in names:
            names.insert(0, best.category_name)
        return names or ["其他"]

    def _keyword_conf(self, best: RuleMatch | None) -> float | None:
        """关键词置信度：命中关键词数量归一化。"""
        if best is None or not best.matched_keywords:
            return 0.0
        return min(1.0, len(best.matched_keywords) / 5.0)

    def _set_suggestion(
        self,
        result: AnalysisResult,
        best: RuleMatch | None,
        ai_result: dict | None,
        text: str,
    ) -> None:
        """确定建议分类与文件名。"""
        # 文档类型：AI 高置信才可覆盖规则；否则以规则命中为准
        doc_type = ""
        if ai_result and ai_result.get("document_type"):
            ai_type = str(ai_result["document_type"]).strip()
            ai_conf = float(ai_result.get("confidence") or 0.0)
            if best and best.category_name:
                # 规则已有明确命中：仅当 AI 高置信且非"其他"时采用 AI，否则信任规则
                if ai_conf >= 0.7 and ai_type and ai_type != "其他":
                    doc_type = ai_type
                else:
                    doc_type = best.category_name
            elif ai_type and ai_type != "其他":
                doc_type = ai_type
        if not doc_type and best:
            doc_type = best.category_name
        result.document_type = doc_type or "其他"

        # 建议分类 path：规则命中优先，否则尝试按 AI 类型匹配分类
        category_path = best.category_path if best else ""
        if not category_path and doc_type:
            category = (
                self.db.query(Category)
                .filter(Category.name == doc_type, Category.enabled.is_(True))
                .first()
            )
            if category:
                category_path = category.path
        result.suggested_category = category_path or "其他"

        # 标题
        title = ""
        if ai_result and ai_result.get("title"):
            title = str(ai_result["title"])
        result.title = title or doc_type

        # 建议文件名：分类模板 或 默认模板
        # 把文档类型注入字段，避免文件名中"类型"位置变成"未识别"
        render_fields = dict(result.field_values)
        if doc_type and "document_type" not in render_fields:
            render_fields["document_type"] = doc_type
        template = self._category_template(category_path) if category_path else DEFAULT_TEMPLATE
        result.suggested_filename = rename_service.render(
            template, render_fields, Path(result.file_path).suffix
        )

    def _category_template(self, category_path: str) -> str:
        category = (
            self.db.query(Category)
            .filter(Category.path == category_path)
            .first()
        )
        if category:
            tpl = (
                self.db.query(RenameTemplate)
                .filter(RenameTemplate.category_id == category.id, RenameTemplate.enabled.is_(True))
                .first()
            )
            if tpl:
                return tpl.template
        return DEFAULT_TEMPLATE
