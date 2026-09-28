"""盖章检测服务（本地图像算法，零 AI token）。

检测原理：
- 公章以红色圆形章为主 → 红色通道掩膜 + 形态学去噪 + 圆形轮廓判定
- 输出三态：sealed（已盖章）/ unsealed（未盖章）/ suspect（疑似，黑白扫描件）
  黑白扫描件红章变灰无法检出，标 suspect 兜底，绝不误判 sealed。
- 支持：图片直接读；PDF 渲染首页 + 末页（盖章通常在落款页），逐页取最优。
- 依赖 numpy + opencv（已随 rapidocr 依赖链安装）；未安装时降级为 skip，不影响识别主流程。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from backend.utils.logger import get_logger

logger = get_logger("services.seal_detector")

try:  # pragma: no cover - 运行环境依赖检测
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

# 图片类型（get_file_type 的取值）
_IMAGE_TYPES = {"jpg", "jpeg", "png", "bmp", "webp"}

# 检测阈值
_MIN_RED_RATIO = 0.0005  # 红色像素占页比例低于此值 → 视为无红（黑白件 → suspect）
_AREA_MIN_RATIO = 0.0005  # 单个章面积占页比例下限
_AREA_MAX_RATIO = 0.20  # 上限（防把整页红头当章）
_CIRCULARITY_MIN = 0.5  # 圆形度下限（4πA/P²）


@dataclass
class SealResult:
    detected: bool = False
    count: int = 0
    confidence: float = 0.0
    status: str = "unsealed"  # sealed / unsealed / suspect / skip
    detail: list = field(default_factory=list)  # [{"cx","cy","r"}, ...]
    skip: bool = False
    error: str | None = None


def _to_bgr(img: Image.Image):
    """PIL Image → OpenCV BGR ndarray。"""
    rgb = img.convert("RGB")
    return np.asarray(rgb)[:, :, ::-1].copy()


def _detect_on_bgr(bgr, page_w: int, page_h: int) -> SealResult:
    if cv2 is None or np is None:
        return SealResult(skip=True, error="opencv 未安装")
    h, w = bgr.shape[:2]
    if h <= 0 or w <= 0:
        return SealResult(error="图像尺寸无效")
    area = w * h

    # 红色掩膜（红色印章：R 高且明显高于 G/B）
    b, g, r = cv2.split(bgr)
    red_mask = ((r > 120) & ((r - g) > 40) & ((r - b) > 40)).astype(np.uint8) * 255
    red_ratio = float(np.count_nonzero(red_mask)) / area

    # 黑白扫描件（红章变灰）：几乎没有红色像素 → 疑似，不误判
    if red_ratio < _MIN_RED_RATIO:
        return SealResult(detected=False, count=0, confidence=0.0, status="suspect",
                          detail=[])

    # 直接找轮廓：JPEG 压缩会在章边缘产生伪影噪点，闭运算会把它们连通成团、
    # 破坏圆形度；改为依赖面积占比下限过滤噪点（小噪点面积远低于 _AREA_MIN_RATIO）
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    seals: list = []
    for c in contours:
        ca = cv2.contourArea(c)
        if ca < area * _AREA_MIN_RATIO or ca > area * _AREA_MAX_RATIO:
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        circularity = 4.0 * np.pi * ca / (peri * peri)
        if circularity < _CIRCULARITY_MIN:
            continue
        (x, y), r = cv2.minEnclosingCircle(c)
        if r < 4:
            continue
        seals.append({"cx": float(x), "cy": float(y), "r": float(r)})

    if seals:
        # 置信度：红色占比越高越可信（0~1 粗估，仅用于展示）
        conf = min(0.99, 0.55 + red_ratio * 25)
        return SealResult(detected=True, count=len(seals), confidence=round(conf, 3),
                          status="sealed", detail=seals)
    # 有红色但没检出圆形章 → 未盖章（红头 / 红字非章）
    return SealResult(detected=False, count=0, confidence=0.0, status="unsealed", detail=[])


def detect_from_image(img: Image.Image, page_w: int | None = None, page_h: int | None = None) -> SealResult:
    """从 PIL 图像检测盖章。page_w/page_h 用于面积占比（默认取图像自身尺寸）。"""
    if cv2 is None:
        return SealResult(skip=True, error="opencv 未安装")
    w, h = img.size
    bgr = _to_bgr(img)
    return _detect_on_bgr(bgr, page_w or w, page_h or h)


def detect_from_file(path: Path) -> SealResult:
    """按文件类型分发：图片直接检测；PDF 渲染首页+末页取最优。"""
    suffix = path.suffix.lower().lstrip(".")
    if suffix in _IMAGE_TYPES:
        try:
            with Image.open(path) as img:
                img.load()
                return detect_from_image(img)
        except Exception as e:  # noqa: BLE001
            logger.warning("盖章检测读图失败 %s: %s", path, e)
            return SealResult(skip=True, error=str(e))

    if suffix == "pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return SealResult(skip=True, error="PyMuPDF 未安装")
        try:
            with fitz.open(str(path)) as doc:
                n = doc.page_count
                if n <= 0:
                    return SealResult(skip=True, error="PDF 无页面")
                pages = {0, n - 1}  # 首页 + 末页（盖章通常在落款页）

                def _rank(s: str) -> int:
                    return {"sealed": 2, "suspect": 1, "unsealed": 0}.get(s, -1)

                best: SealResult | None = None
                for idx in pages:
                    page = doc.load_page(idx)
                    pm = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
                    img = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
                    res = detect_from_image(img)
                    # 优先级 sealed > suspect > unsealed（suspect 不能被初始 unsealed 吞掉）
                    if best is None or _rank(res.status) > _rank(best.status):
                        best = res
                    if res.status == "sealed":
                        break
                return best or SealResult(detected=False, count=0, status="unsealed")
        except Exception as e:  # noqa: BLE001
            logger.warning("盖章检测 PDF 失败 %s: %s", path, e)
            return SealResult(skip=True, error=str(e))

    # Word / Excel / 其他：跳过
    return SealResult(skip=True)
