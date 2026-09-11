"""fingerprint_service.py：核心文档指纹算法服务（V5 步骤 3）。

纯 Python / 本地 CPU 计算，零 Token、零外部模型调用。用于：
- 同类文件自动聚类（版面网格哈希 + 结构关键词 SimHash → 海明距离判定）
- 与识别模板绑定（同一版面指纹 → 批量免审 / 直接套用模板字段提取）

指纹构成：
1. layout_hash   版面拓扑哈希：首页按 10x10 网格离散化文本块密度 → SHA256
2. text_simhash  结构关键词 64 位 SimHash（md5 逐词加权投票）
3. feature_meta  补充元数据：页数 / 宽高比 / 文本块数 / 命中的结构关键词

注意：
- 仅针对 PDF（fitz/PyMuPDF）；扫描件（无文本层）块数为 0，指纹会退化为空网格，
  后续可扩展 OCR 版面特征通道（图片格式同理）。
- 相似判定：海明距离 <= 5（可调 max_distance）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import fitz  # PyMuPDF

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


class FingerprintService:
    """本地文档指纹提取与相似度比对服务（零 Token）。"""

    STRUCTURE_KEYWORDS = STRUCTURE_KEYWORDS

    @classmethod
    def extract_from_pdf(cls, file_path: str) -> tuple[str, str, dict[str, Any]]:
        """从 PDF 提取版面拓扑哈希与结构 SimHash。

        Returns:
            (layout_hash, text_simhash, meta)
            - layout_hash: 64 位十六进制字符串（版面网格密度 SHA256 截断）
            - text_simhash: 64 位十六进制字符串（结构关键词 SimHash）
            - meta: 补充特征（page_count / aspect_ratio / block_count / matched_keywords）
        """
        doc = fitz.open(file_path)
        try:
            if len(doc) == 0:
                return "", "", {}

            first_page = doc[0]
            rect = first_page.rect
            width, height = rect.width, rect.height
            blocks = first_page.get_text("blocks")

            # 1) 版面网格拓扑哈希：将页面按 10x10 网格离散化文本块密度
            grid = [0] * (_GRID_SIZE * _GRID_SIZE)
            tokens: list[str] = []

            for b in blocks:
                x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
                # 归一化坐标并映射至 10x10 网格
                gx = min(int((x0 / width) * _GRID_SIZE), _GRID_SIZE - 1) if width else 0
                gy = min(int((y0 / height) * _GRID_SIZE), _GRID_SIZE - 1) if height else 0
                grid[gy * _GRID_SIZE + gx] += 1

                # 过滤数字/空白/标点后收集结构关键词
                cleaned = re.sub(r"[\d\s\-_:：/\\,.，。]", "", text)
                for kw in cls.STRUCTURE_KEYWORDS:
                    if kw in cleaned:
                        tokens.append(kw)

            # 生成 64 字符版面哈希（密度截断到 0-9，防止长文本块权重失衡）
            layout_str = ",".join(str(min(v, 9)) for v in grid)
            layout_hash = hashlib.sha256(layout_str.encode("utf-8")).hexdigest()

            # 2) 结构关键词 SimHash（64 位）
            text_simhash = cls._compute_simhash(tokens)

            meta = {
                "page_count": len(doc),
                "aspect_ratio": round(height / width, 2) if width else 0,
                "block_count": len(blocks),
                "matched_keywords": sorted(set(tokens)),
            }
            return layout_hash, text_simhash, meta
        finally:
            doc.close()

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
