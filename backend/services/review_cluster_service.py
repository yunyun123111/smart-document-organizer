"""review_cluster_service.py：待审核单据指纹聚类与批量继承（V5 步骤）。

职责：
1. get_clustered_pending_reviews —— 待审核（need_review）文档按"文档指纹"聚类，
   同一版面/高度相似结构 → 归为一簇（一次审核，全组应用）。
2. batch_confirm_cluster —— 以基准文档的分类，批量确认并归档整组文档，
   归档逻辑复用人工审核的既有流程（ArchiveService + 模板学习），保证行为一致。

指纹聚类判定（与 FingerprintService 一致）：
- 版面一致（layout_hash 相同），或
- 结构 SimHash 海明距离 <= 5

说明：
- 待审核状态为 STATUS_NEED_REVIEW（"need_review"），非 pending_review；
- 分类存于 DocumentField.suggested_category（无 category_id 列）；
- 指纹缺失时按文件类型即时计算（PDF→extract_from_pdf，图片→extract_from_image），
  计算成功写入 document_fingerprints；docx/xlsx 等暂不支持指纹，独立成簇。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, joinedload

from backend.models import (
    STATUS_NEED_REVIEW,
    Document,
    DocumentFingerprint,
)
from backend.services.archive_service import ArchiveService
from backend.services.fingerprint_service import FingerprintService
from backend.utils.logger import get_logger

# 复用人工审核流程的既有实现（渲染建议名 / 字段落库 / 模板学习），避免逻辑漂移
from backend.services.review_support import (
    _fields_dict,
    _learn_template,
    _render_suggestion,
    _upsert_fields,
)

logger = get_logger("review_cluster")

# 支持指纹提取的文件类型（其余类型独立成簇）
_FINGERPRINTABLE_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}


class ReviewClusterService:
    """待审核单据指纹聚类与批量确认服务。"""

    # ---------------------------------------------------------------- 聚类

    @classmethod
    def get_clustered_pending_reviews(cls, db: Session) -> list[dict[str, Any]]:
        """获取按指纹聚类后的待审核文档列表。

        Returns:
            簇列表，按成员数降序；每簇含：
            cluster_id / representative_doc / count / documents
        """
        docs = (
            db.query(Document)
            .options(joinedload(Document.fields))
            .filter(Document.status == STATUS_NEED_REVIEW)
            .order_by(Document.created_at.desc())
            .all()
        )
        if not docs:
            return []

        clusters: list[dict[str, Any]] = []
        pending_fps: list[DocumentFingerprint] = []

        for doc in docs:
            fp = cls._get_or_compute_fingerprint(db, doc, pending_fps)

            matched = None
            if fp:
                for c in clusters:
                    base_fp = c["base_fingerprint"]
                    # 版面一致 或 结构 SimHash 距离 <= 5 → 同类单据
                    if (
                        base_fp.layout_hash
                        and base_fp.layout_hash == fp.layout_hash
                    ) or FingerprintService.is_similar(
                        base_fp.text_simhash, fp.text_simhash, max_distance=5
                    ):
                        matched = c
                        break

            if matched is not None:
                matched["documents"].append(cls._doc_to_dict(doc))
                matched["count"] += 1
            else:
                clusters.append(
                    {
                        "cluster_id": f"cluster_{doc.id}",
                        "base_fingerprint": fp,
                        "representative_doc": cls._doc_to_dict(doc),
                        "count": 1,
                        "documents": [cls._doc_to_dict(doc)],
                    }
                )

        # 即时计算的指纹统一落库（批量提交，减少磁盘写）
        if pending_fps:
            db.add_all(pending_fps)
            db.commit()
            logger.info("聚类时新增指纹 %d 条", len(pending_fps))

        # 移除内部对象，返回前端安全结构
        for c in clusters:
            c.pop("base_fingerprint", None)

        return sorted(clusters, key=lambda x: x["count"], reverse=True)

    @classmethod
    def _get_or_compute_fingerprint(
        cls,
        db: Session,
        doc: Document,
        pending_fps: list[DocumentFingerprint],
    ) -> DocumentFingerprint | None:
        """获取已存指纹；缺失且文件可指纹化则即时计算（延迟到批量提交）。"""
        fp = (
            db.query(DocumentFingerprint)
            .filter(DocumentFingerprint.document_id == doc.id)
            .first()
        )
        if fp is not None:
            return fp

        source = Path(doc.current_path or doc.original_path)
        ext = source.suffix.lower()
        if ext not in _FINGERPRINTABLE_EXTS or not source.exists():
            return None

        try:
            if ext == ".pdf":
                layout_hash, simhash, meta = FingerprintService.extract_from_pdf(str(source))
            else:
                layout_hash, simhash, meta = FingerprintService.extract_from_image(str(source))
        except Exception:  # noqa: BLE001
            logger.warning("指纹计算失败: %s", doc.id)
            return None

        if not simhash or simhash == "0" * 16:
            # 无文本层且 OCR 不可用：指纹无区分度，不落库（保持独立成簇）
            return None

        new_fp = DocumentFingerprint(
            document_id=doc.id,
            layout_hash=layout_hash,
            text_simhash=simhash,
            feature_meta=meta,
        )
        pending_fps.append(new_fp)
        return new_fp

    # ---------------------------------------------------------------- 批量继承归档

    @classmethod
    def batch_confirm_cluster(
        cls,
        db: Session,
        base_doc_id: int,
        target_doc_ids: list[int],
        category_path: str | None = None,
        save_as_sample: bool = False,
    ) -> dict[str, Any]:
        """以基准文档的分类，批量确认并归档整组文档。

        Args:
            base_doc_id: 基准文档（代表簇），其分类作为整组统一分类
            target_doc_ids: 目标文档 id 列表（通常为簇内其余文档）
            category_path: 可选覆盖分类；缺省取基准文档的 suggested_category
            save_as_sample: 预留：是否额外将基准文档保存为格式样本

        Returns:
            {"total", "success_count", "failed_count", "results": [...]}
        """
        base = db.get(Document, base_doc_id)
        if base is None:
            raise ValueError(f"基准文档不存在: {base_doc_id}")
        base_fields = _fields_dict(base)
        category_path = category_path or base_fields.get("suggested_category") or "其他"

        results: list[dict] = []
        for doc_id in target_doc_ids:
            doc = (
                db.query(Document)
                .options(joinedload(Document.fields))
                .filter(Document.id == doc_id, Document.status == STATUS_NEED_REVIEW)
                .first()
            )
            if doc is None:
                results.append({"id": doc_id, "success": False, "error": "不存在或不在待审核列表"})
                continue

            try:
                fields = _fields_dict(doc)
                fields["suggested_category"] = category_path
                _upsert_fields(db, doc, {"suggested_category": category_path})
                db.commit()  # 先落库字段与分类，归档失败时不丢失

                filename = _render_suggestion(db, doc, category_path, fields)
                source = Path(doc.current_path)
                if not source.exists():
                    results.append({"id": doc_id, "success": False, "error": "原文件已不存在，无法归档"})
                    continue

                archive = ArchiveService(db)
                ar = archive.archive(
                    source,
                    category_path=category_path,
                    filename=filename,
                    date_str=fields.get("date"),
                    document_id=doc.id,
                )
                if ar.success:
                    _learn_template(db, doc, category_path)
                    results.append({"id": doc_id, "success": True, "path": str(ar.document_path)})
                else:
                    results.append({"id": doc_id, "success": False, "error": ar.error or "归档失败"})
            except Exception as e:  # noqa: BLE001
                logger.exception("簇内文档归档失败 doc_id=%s", doc_id)
                results.append({"id": doc_id, "success": False, "error": str(e)})

        success = sum(1 for r in results if r["success"])
        logger.info(
            "批量继承归档完成: base=%s category=%s %d/%d 成功",
            base_doc_id, category_path, success, len(results),
        )
        return {
            "total": len(target_doc_ids),
            "success_count": success,
            "failed_count": len(results) - success,
            "results": results,
        }

    # ---------------------------------------------------------------- 工具

    @staticmethod
    def _doc_to_dict(doc: Document) -> dict[str, Any]:
        fields = _fields_dict(doc)
        return {
            "id": doc.id,
            "filename": doc.original_filename,
            "document_type": doc.document_type,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "confidence": doc.confidence,
            "suggested_category": fields.get("suggested_category"),
            "file_size": doc.file_size,
        }
