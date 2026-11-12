"""单据判重指纹（dHash）测试：同图识别 + 不同单据不误判。"""
from __future__ import annotations

from decimal import Decimal

from PIL import Image

from backend.services.doc_dedup import fingerprint, hamming, is_same_image
from backend.services.reconciliation_service import DocRecord, _same_image_record


def _make_png(path, size=(64, 48), left=255, right=0):
    """左半亮、右半暗的图，便于产生稳定的 dHash。"""
    img = Image.new("L", size, 255)
    for x in range(size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), left if x < size[0] // 2 else right)
    img.save(path)
    return str(path)


class TestHamming:
    def test_same_hash(self):
        assert hamming("161f2d2e2f362400", "161f2d2e2f362400") == 0

    def test_one_bit(self):
        assert hamming("161f2d2e2f362400", "161f2d2e2f362401") == 1

    def test_invalid(self):
        assert hamming("not-a-hash", "161f2d2e2f362400") == 99


class TestIsSameImage:
    def test_identical(self):
        assert is_same_image(("161f2d2e2f362400", 1280, 1852),
                             ("161f2d2e2f362400", 1280, 1852)) is True

    def test_size_must_match(self):
        # 尺寸不同一律不算同一张（缩放后的重拍交给业务键去重）
        assert is_same_image(("161f2d2e2f362400", 1280, 1852),
                             ("161f2d2e2f362400", 640, 926)) is False

    def test_distance_two_is_rejected(self):
        # 实测两张同版式不同金额发票差 2 位，不能被当成同一张
        assert is_same_image(("cdcd22ea009a9840", 827, 552),
                             ("cdcd2aea009a8840", 827, 552)) is False

    def test_missing_fingerprint(self):
        assert is_same_image(None, ("161f2d2e2f362400", 1280, 1852)) is False


class TestFingerprint:
    def test_reads_image(self, tmp_path):
        p = _make_png(tmp_path / "a.png")
        fp = fingerprint(p)
        assert fp is not None
        assert len(fp[0]) == 16
        assert fp[1:] == (64, 48)

    def test_missing_file(self, tmp_path):
        assert fingerprint(str(tmp_path / "nope.png")) is None
        assert fingerprint(None) is None


class TestSameImageRecord:
    def test_same_file_different_extracted_amount_is_not_duplicate(self):
        """同一张图但两边金额都读出且不同 —— 可能是两张同版式单据，不算重复。"""
        a = DocRecord(document_id=1, phash="161f2d2e2f362400", img_w=1280, img_h=1852,
                      amount=Decimal("7922962.05"))
        b = DocRecord(document_id=2, phash="161f2d2e2f362400", img_w=1280, img_h=1852,
                      amount=Decimal("7990500.00"))
        assert _same_image_record(a, b) is False

    def test_same_file_ignores_business_key(self):
        """业务键（合同号/日期）缺失时，指纹仍然认出是同一张图。"""
        a = DocRecord(document_id=5, filename="A.jpg", phash="161f2d2e2f362400",
                      img_w=1280, img_h=1852, amount=Decimal("7922962.05"))
        b = DocRecord(document_id=17, filename="B.jpg", phash="161f2d2e2f362400",
                      img_w=1280, img_h=1852, amount=Decimal("7922962.05"))
        assert _same_image_record(a, b) is True

    def test_different_images(self):
        a = DocRecord(document_id=1, phash="cdcd22ea009a9840", img_w=827, img_h=552)
        b = DocRecord(document_id=7, phash="cdcd2aea009a8840", img_w=827, img_h=552)
        assert _same_image_record(a, b) is False
