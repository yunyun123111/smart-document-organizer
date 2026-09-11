"""fingerprint_service.py：核心文档指纹算法服务（V5 步骤 3 + OCR 通道扩展）。

纯 Python / 本地 CPU 计算，零 Token、零外部模型调用。用于：
- 同类文件自动聚类（版面网格哈希 + 结构关键词 SimHash → 海明距离判定）
- 与识别模板绑定（同一版面指纹 → 批量免审 / 直接套用模板字段提取）

指纹构成：
1. layout_hash   版面拓扑哈希：首页按 10x10 网格离散化文本块密度 → SHA256
2. text_simhash  结构关键词 64 位 SimHash（md5 逐词加权投票）
3. feature_meta  补充元数据：页数 / 宽高比 / 块数 / 命中的结构关键词 / 指纹来源

指纹来源（source）三级策略：
- text_layer：PDF 有文本层 → 直接取文本块（最快、零 OCR 开销）
- ocr       ：PDF 无文本层（扫描件）或图片 → 本地 RapidOCR 识别文本与坐标，
             用 OCR 文本框坐标算版面网格、OCR 文本算 SimHash（区分度与真实版面一致）
- empty     ：OCR 未启用 / 无任何可识别内容 → 指纹退化为全 0，标记不可用

OCR 通道性能：200 DPI 渲染 + RapidOCR，单页约 1~3 秒；多页文档限制最多
OCR 前 _MAX_OCR_PAGES 页，防止超长扫描件卡死。
"""
from __future__ import annotations

import hashlib
import io
import re
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

from backend.services.ocr_service import OCRItem, ocr_service
from backend.utils.logger import get_logger

logger = get_logger("fingerprint")

# 结构关键词：业务单据的高区分度词，命中即计入 SimHash 投票
STRUCTURE_KEYWORDS: list[str] = [
    "合同", "协议", "结算", "发票", "拉运", "对账", "明细",
    "船名", "航次", "提单号", "合同号", "金额", "单价", "数量",
    "买方", "卖方", "供方", "需方", "装货港", "卸货港", "税额",
    "合计", "开票日期", "扣水", "扣杂", "净重", "毛重",
]

# 页面离散化网格尺寸（10x10）
_GRID_SIZE = 10
# OCR 通道最多处理的页数（防止超长扫描件卡死）
_MAX_OCR_PAGES = 10
# OCR 渲染分辨率（与 ocr_service.recognize_pdf 一致：200 DPI）
_OCR_DPI = 200


class FingerprintService:
    """本地文档指纹提取与相似度比对服务（零 Token）。"""

    STRUCTURE_KEYWORDS = STRUCTURE_KEYWORDS

    # ---------------------------------------------------------------- 对外入口

    @classmethod
    def extract_from_pdf(cls, file_path: str) -> tuple[str, str, dict[str, Any]]:
        """从 PDF 提取版面拓扑哈希与结构 SimHash（自动选择指纹来源）。

        优先文本层（零 OCR 开销）；无文本层时自动降级到本地 OCR 通道。

        Returns:
            (layout_hash, text_simhash, meta)
            - layout_hash: 64 位十六进制字符串（版面网格密度 SHA256 截断）
            - text_simhash: 64 位十六进制字符串（结构关键词 SimHash）
            - meta: 补充特征（page_count / aspect_ratio / block_count /
                    matched_keywords / source / ocr_confidence）
        """
        doc = fitz.open(file_path)
        try:
            if len(doc) == 0:
                return "", "", {}

            first_page = doc[0]
            rect = first_page.rect
            width, height = rect.width, rect.height
            blocks = first_page.get_text("blocks")
            text_blocks = [b for b in blocks if b[4].strip()]

            # 文本层可用性判定：避免"扫描图 + 1 个无意义文本块"误入 text_layer。
            # 判定为可用需满足：命中结构关键词，或文本量充足（>=30 字符）且至少 2 个文本块。
            if text_blocks and cls._text_layer_usable(text_blocks):
                return cls._extract_from_text_blocks(
                    text_blocks, page_count=len(doc), width=width, height=height
                )

            # 无文本层（扫描件）→ 本地 OCR 通道
            return cls._extract_pdf_via_ocr(doc)
        finally:
            doc.close()

    @classmethod
    def extract_from_image(cls, file_path: str) -> tuple[str, str, dict[str, Any]]:
        """从图片（JPG/PNG 扫描件）提取版面拓扑哈希与结构 SimHash。

        全程本地 OCR，零 Token。
        """
        with Image.open(file_path) as img:
            width, height = img.size
            res = ocr_service.recognize_image(img)

        meta_base = {
            "page_count": 1,
            "aspect_ratio": round(height / width, 2) if width else 0,
            "source": "ocr" if res.text else "empty",
            "ocr_confidence": round(res.confidence, 3),
        }
        if not res.items:
            meta_base.update(block_count=0, matched_keywords=[])
            logger.warning("图片 OCR 无结果：%s", file_path)
            return cls._empty_fingerprints(meta_base)
        return cls._fingerprints_from_ocr_items(
            res.items, width, height, meta_base
        )

    # ---------------------------------------------------------------- 文本层路径

    @staticmethod
    def _text_layer_usable(text_blocks: list) -> bool:
        """文本层是否可用（命中结构关键词，或文本量充足且块数>=2）。"""
        full_text = "".join(b[4] for b in text_blocks)
        kw_hits = any(kw in full_text for kw in STRUCTURE_KEYWORDS)
        return kw_hits or (
            len(re.sub(r"[\s\d]", "", full_text)) >= 30
            and len(text_blocks) >= 2
        )

    @classmethod
    def _extract_from_text_blocks(
        cls,
        blocks: list,
        page_count: int,
        width: float,
        height: float,
    ) -> tuple[str, str, dict[str, Any]]:
        """文本层指纹：文本块坐标 → 版面网格；文本关键词 → SimHash。"""
        grid = [0] * (_GRID_SIZE * _GRID_SIZE)
        tokens: list[str] = []

        for b in blocks:
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            gx = min(int((x0 / width) * _GRID_SIZE), _GRID_SIZE - 1) if width else 0
            gy = min(int((y0 / height) * _GRID_SIZE), _GRID_SIZE - 1) if height else 0
            grid[gy * _GRID_SIZE + gx] += 1
            cleaned = re.sub(r"[\d\s\-_:：/\\,.，。]", "", text)
            for kw in cls.STRUCTURE_KEYWORDS:
                if kw in cleaned:
                    tokens.append(kw)

        layout_hash = cls._hash_grid(grid)
        meta = {
            "page_count": page_count,
            "aspect_ratio": round(height / width, 2) if width else 0,
            "block_count": len(blocks),
            "matched_keywords": sorted(set(tokens)),
            "source": "text_layer",
            "ocr_confidence": 1.0,
        }
        return layout_hash, cls._compute_simhash(tokens), meta

    # ---------------------------------------------------------------- OCR 路径

    @classmethod
    def _extract_pdf_via_ocr(cls, doc) -> tuple[str, str, dict[str, Any]]:
        """扫描 PDF：渲染页面 → RapidOCR → 版面网格（首页 box 坐标）+ SimHash（全页文本）。

        首页用于版面网格（与文本层逻辑对齐）；全部页文本参与结构关键词投票，
        保证多页单据（合同）结构特征完整。
        """
        page_count = len(doc)
        first_page = doc[0]
        rect = first_page.rect
        width, height = rect.width, rect.height

        # 首页渲染 → OCR（版面网格数据源）
        pix = first_page.get_pixmap(dpi=_OCR_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        first_res = ocr_service.recognize_image(img)

        meta_base = {
            "page_count": page_count,
            "aspect_ratio": round(height / width, 2) if width else 0,
            "source": "ocr" if first_res.text else "empty",
            "ocr_confidence": round(first_res.confidence, 3),
        }
        if not first_res.items:
            # 首页无 OCR 结果 → 指纹不可用
            meta_base.update(block_count=0, matched_keywords=[])
            logger.warning("扫描 PDF 首页 OCR 无结果：%s", doc.name)
            return cls._empty_fingerprints(meta_base)

        # 其余页（最多 _MAX_OCR_PAGES - 1 页）也 OCR，文本并入 SimHash
        tokens: list[str] = []
        for page_index in range(1, min(page_count, _MAX_OCR_PAGES)):
            try:
                page = doc.load_page(page_index)
                pix = page.get_pixmap(dpi=_OCR_DPI)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                res = ocr_service.recognize_image(img)
                tokens.extend(cls._collect_keywords(res.items))
            except Exception as e:  # noqa: BLE001
                logger.warning("扫描 PDF 第 %d 页 OCR 失败，跳过：%s", page_index + 1, e)

        # 首页 items 同时提供版面网格 + 关键词
        grid = cls._grid_from_items(first_res.items, width, height)
        tokens.extend(cls._collect_keywords(first_res.items))

        layout_hash = cls._hash_grid(grid)
        meta = dict(meta_base)
        meta.update(
            block_count=len(first_res.items),
            matched_keywords=sorted(set(tokens)),
        )
        return layout_hash, cls._compute_simhash(tokens), meta

    @classmethod
    def _fingerprints_from_ocr_items(
        cls,
        items: list[OCRItem],
        width: float,
        height: float,
        meta_base: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        """由 OCR 文本框（含四点坐标）计算版面网格与 SimHash。"""
        grid = cls._grid_from_items(items, width, height)
        tokens = cls._collect_keywords(items)
        meta = dict(meta_base)
        meta.update(
            block_count=len(items),
            matched_keywords=sorted(set(tokens)),
        )
        return cls._hash_grid(grid), cls._compute_simhash(tokens), meta

    # ---------------------------------------------------------------- 公共工具

    @staticmethod
    def _grid_from_items(items: list[OCRItem], width: float, height: float) -> list[int]:
        """OCR 文本框中心点 → 10x10 网格密度（宽高按渲染图/页面归一化）。"""
        grid = [0] * (_GRID_SIZE * _GRID_SIZE)
        for it in items:
            box = it.box
            if not box or len(box) < 4:
                continue
            # 四点坐标（左上/右上/右下/左下），取对角点中心
            cx = (box[0][0] + box[2][0]) / 2
            cy = (box[0][1] + box[2][1]) / 2
            gx = min(int((cx / width) * _GRID_SIZE), _GRID_SIZE - 1) if width else 0
            gy = min(int((cy / height) * _GRID_SIZE), _GRID_SIZE - 1) if height else 0
            grid[gy * _GRID_SIZE + gx] += 1
        return grid

    @classmethod
    def _collect_keywords(cls, items: list[OCRItem]) -> list[str]:
        """从 OCR 文本框提取结构关键词（去数字/标点后匹配）。"""
        tokens: list[str] = []
        for it in items:
            cleaned = re.sub(r"[\d\s\-_:：/\\,.，。]", "", it.text or "")
            for kw in cls.STRUCTURE_KEYWORDS:
                if kw in cleaned:
                    tokens.append(kw)
        return tokens

    @staticmethod
    def _hash_grid(grid: list[int]) -> str:
        """网格密度（截断 0-9）→ SHA256 十六进制。"""
        layout_str = ",".join(str(min(v, 9)) for v in grid)
        return hashlib.sha256(layout_str.encode("utf-8")).hexdigest()

    @staticmethod
    def _empty_fingerprints(meta: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        """指纹不可用：版面空网格哈希 + SimHash 全 0。"""
        return FingerprintService._hash_grid([0] * 100), "0" * 16, meta

    @staticmethod
    def _compute_simhash(tokens: list[str]) -> str:
        """计算特征词的 64 位 SimHash，返回 16 位十六进制字符串。

        每个 token 用 md5 映射到 64 位向量并加权投票（1 加 / 0 减），
        最终按位取多数得到 64 位指纹。空输入返回全 0。
        """
        if not tokens:
            return "0" * 16  # 64 位全 0，十六进制 16 字符

        v = [0] * 64
        for token in tokens:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:16], 16)
            for i in range(64):
                bit = (h >> i) & 1
                v[i] += 1 if bit else -1

        fingerprint = 0
        for i in range(64):
            if v[i] > 0:
                fingerprint |= 1 << i
        return f"{fingerprint:016x}"

    @classmethod
    def hamming_distance(cls, hash1: str, hash2: str) -> int:
        """计算两个 16 进制 SimHash 之间的海明距离；非法输入返回 64（最远）。"""
        try:
            val1 = int(hash1, 16)
            val2 = int(hash2, 16)
            x = val1 ^ val2
            return bin(x).count("1")
        except (ValueError, TypeError):
            return 64

    @classmethod
    def is_similar(cls, hash1: str, hash2: str, max_distance: int = 5) -> bool:
        """海明距离 <= max_distance 判定为同一结构特征单据。"""
        return cls.hamming_distance(hash1, hash2) <= max_distance
