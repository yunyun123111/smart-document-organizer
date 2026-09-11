"""核心文档指纹算法服务测试（V5 步骤 3）。

覆盖：
- SimHash 计算：空输入全 0、相同关键词集合距离小、不同集合距离大
- 海明距离 / is_similar 阈值判定、非法输入容错
- PDF 指纹提取：layout_hash / text_simhash 非空、meta 字段齐全、同一版面可复现

SimHash 特性说明（按实际算法校准）：
- 每个词用 md5 映射为 64 位向量等权投票，词集合变化会翻转多位；
  实测：4/5 关键词重叠 → 距离约 11；完全不同集合 → 距离 > 30。
  因此相似判定阈值应结合具体使用场景（同类单据关键词高度重叠时距离通常 <= 15）。
"""
from __future__ import annotations

import os
import tempfile

import fitz

from backend.services.fingerprint_service import FingerprintService


class TestSimHash:
    def test_empty_tokens_all_zero(self):
        assert FingerprintService._compute_simhash([]) == "0" * 16

    def test_same_keywords_close_distance(self):
        h1 = FingerprintService._compute_simhash(["合同", "金额", "船名", "买方"])
        h2 = FingerprintService._compute_simhash(["合同", "金额", "船名", "买方", "卖方"])
        # 关键词重叠 4/5，海明距离应明显小于完全不同集合
        assert FingerprintService.hamming_distance(h1, h2) <= 15

    def test_different_keywords_far_distance(self):
        h1 = FingerprintService._compute_simhash(["合同", "金额", "船名"])
        h2 = FingerprintService._compute_simhash(["发票", "税额", "开票日期"])
        # 完全不同集合：距离显著大于重叠集合（实测 > 30）
        assert FingerprintService.hamming_distance(h1, h2) > 20

    def test_is_similar_threshold(self):
        h1 = FingerprintService._compute_simhash(["合同", "结算单", "船名"])
        h2 = FingerprintService._compute_simhash(["合同", "结算单", "船名", "净重"])
        # 高度重叠集合在宽松阈值下判定相似
        assert FingerprintService.is_similar(h1, h2, max_distance=15) is True
        # 完全不同集合即使在宽松阈值下也不相似
        h3 = FingerprintService._compute_simhash(["发票", "税额"])
        assert FingerprintService.is_similar(h1, h3, max_distance=15) is False

    def test_hamming_distance_invalid_input(self):
        assert FingerprintService.hamming_distance("zzz", "abc") == 64
        assert FingerprintService.hamming_distance("", None) == 64

    def test_is_similar_exact_same(self):
        h = FingerprintService._compute_simhash(["合同", "金额"])
        assert FingerprintService.is_similar(h, h) is True


class TestExtractFromPdf:
    def _make_pdf(self, texts: list[str]) -> str:
        """构造带指定文本的首页 PDF（中文字体 china-s），返回临时路径。"""
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)  # A4
        y = 80
        for t in texts:
            page.insert_text((72, y), t, fontsize=12, fontname="china-s")
            y += 30
        doc.save(path)
        doc.close()
        return path

    def test_extract_returns_fingerprints_and_meta(self):
        path = self._make_pdf(
            ["销售合同", "合同号：SJWLXS-2026-YC0453", "船名：海丰轮", "买方：宏达", "金额：100000"]
        )
        try:
            layout_hash, simhash, meta = FingerprintService.extract_from_pdf(path)
            assert layout_hash and len(layout_hash) == 64
            assert simhash and len(simhash) == 16  # 64 位十六进制
            assert meta["page_count"] == 1
            assert meta["aspect_ratio"] > 0
            assert meta["block_count"] >= 1
            assert "合同" in meta["matched_keywords"]
            assert "船名" in meta["matched_keywords"]
        finally:
            os.remove(path)

    def test_same_layout_same_fingerprint(self):
        texts = ["销售合同", "合同号：SJWLXS-2026-YC0453", "船名：海丰轮"]
        p1 = self._make_pdf(texts)
        p2 = self._make_pdf(texts)
        try:
            l1, s1, _ = FingerprintService.extract_from_pdf(p1)
            l2, s2, _ = FingerprintService.extract_from_pdf(p2)
            assert l1 == l2  # 版面布局一致 → 拓扑哈希一致
            assert s1 == s2  # 结构关键词一致 → SimHash 一致
        finally:
            os.remove(p1)
            os.remove(p2)

    def test_no_text_page_returns_zero_simhash(self):
        """无文本页：无关键词 → SimHash 全 0；版面哈希仍可生成。"""
        path = self._make_pdf([])  # 1 页无文本
        try:
            layout_hash, simhash, meta = FingerprintService.extract_from_pdf(path)
            assert layout_hash and len(layout_hash) == 64
            assert simhash == "0" * 16
            assert meta["block_count"] == 0
        finally:
            os.remove(path)
