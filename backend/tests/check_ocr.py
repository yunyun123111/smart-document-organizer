"""临时验证脚本：验证 RapidOCR 中文识别（里程碑 2 / P4 验证用）。"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.ocr_service import OCRService  # noqa: E402


def _font(size: int):
    for p in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = Image.new("RGB", (800, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((30, 40), "增值税专用发票", font=_font(48), fill="black")
    draw.text((30, 120), "发票号码：12345678", font=_font(36), fill="black")
    draw.text((30, 190), "金额：人民币128500元  日期：2026年8月20日", font=_font(32), fill="black")

    out = ROOT / "data/temp/ocr_sample.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)

    svc = OCRService()
    result = svc.recognize_image_file(out)
    print("OCR 文本:")
    print(result.text)
    print(f"置信度: {result.confidence:.3f}, 识别项: {len(result.items)}")
    assert "发票" in result.text, "未能识别出'发票'"
    print("验证通过 ✔")


if __name__ == "__main__":
    main()
