"""盖章检测服务（本地图像算法，零 AI token）。

检测原理（v2，解决旧版"圆环碎片导致零召回"）：
- 红章由「圆环 + 环形文字 + 五角星」组成，红色像素是碎片而非实心盘。
  直接对外轮廓算圆形度，得到的是细环（周长极大、面积很小），圆形度趋近 0，
  所以旧版把所有真章都滤掉了。
- 现在的做法：宽红掩膜（HSV 双区间 + RGB 差值补充）→ 椭圆核闭运算把碎片连成
  实心块 → 再按面积占比 / 圆形度 / 填充率 / 长宽比四重判据筛章。
- 输出三态：sealed（已盖章）/ unsealed（未盖章）/ suspect（疑似，黑白扫描件）
  黑白扫描件红章变灰无法检出，标 suspect 兜底，绝不误判 sealed。
- 支持：图片直接读；PDF 扫描首页 + 末页 + 倒数第二页（盖章通常在落款页），逐页取最优。
- 实测命中：采购合同末页双章、上游结算单、下游结算单双章、退款回单、放货通知；
  纯电子回单（无实体红章）正确判为 unsealed。
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

# 检测阈值（v2，实测标定：真章 circ .6~.89 / fill .69~.79 / aspect 1.0~1.05）
_MIN_RED_RATIO = 0.0008  # 红色像素占页比例低于此值 → 视为无红（黑白件 → suspect）
_AREA_MIN_RATIO = 0.003  # 单个章面积占页比例下限（滤掉噪点与文字碎片）
_AREA_MAX_RATIO = 0.30  # 上限（防把整页红底/红头当章）
_CIRCULARITY_MIN = 0.5  # 圆形度下限（4πA/P²）
_FILL_MIN = 0.6  # 填充率下限（面积 / 外接矩形面积）：真章闭运算后接近实心
_ASPECT_MAX = 1.6  # 长宽比上限：竖排红字条 aspect 约 2.2，真章约 1.0


def _build_red_mask(bgr):
    """宽红掩膜：HSV 双区间（正红/偏品红）+ RGB 差值补充（弱红、扫描褪色）。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 30, 25], dtype=np.uint8),
                     np.array([14, 255, 255], dtype=np.uint8))
    m2 = cv2.inRange(hsv, np.array([166, 30, 25], dtype=np.uint8),
                     np.array([180, 255, 255], dtype=np.uint8))
    mask = cv2.bitwise_or(m1, m2)
    b, g, r = cv2.split(bgr)
    diff = ((r > 110) & ((r.astype(np.int16) - g.astype(np.int16)) > 30)
            & ((r.astype(np.int16) - b.astype(np.int16)) > 30)).astype(np.uint8) * 255
    return cv2.bitwise_or(mask, diff)


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

    mask = _build_red_mask(bgr)
    red_ratio = float(np.count_nonzero(mask)) / area

    # 黑白扫描件（红章变灰）：几乎没有红色像素 → 疑似，不误判
    if red_ratio < _MIN_RED_RATIO:
        return SealResult(detected=False, count=0, confidence=0.0, status="suspect", detail=[])

    # 闭运算：红章是「圆环 + 环形文字 + 五角星」碎片，直接把外轮廓当章会得到
    # 极细的环（周长极大、面积很小 → 圆形度接近 0），全部被滤掉。先闭运算把
    # 碎片连成实心块，再算形状判据。
    short = min(h, w)
    k = max(9, int(short * 0.012))
    if k % 2 == 0:
        k += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

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
        x, y, cw, ch = cv2.boundingRect(c)
        if cw <= 0 or ch <= 0:
            continue
        fill = ca / float(cw * ch)
        if fill < _FILL_MIN:
            continue
        aspect = max(cw, ch) / float(min(cw, ch))
        if aspect > _ASPECT_MAX:
            continue
        seals.append({
            "cx": float(x + cw / 2), "cy": float(y + ch / 2),
            "r": float(max(cw, ch) / 2), "w": int(cw), "h": int(ch),
            "circularity": round(float(circularity), 3),
            "fill": round(float(fill), 3),
            "area_ratio": round(float(ca / area), 4),
        })

    if seals:
        # 置信度：形状质量均值（圆形度 + 填充率）/2，映射到 0.55~0.99
        avg = sum((s["circularity"] + s["fill"]) / 2 for s in seals) / len(seals)
        conf = min(0.99, 0.55 + avg * 0.5)
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
                # 盖章通常在落款页：优先末页，再倒数第二页，最后首页（省渲染时间）
                idxs: list[int] = []
                for i in (n - 1, n - 2, 0):
                    if 0 <= i < n and i not in idxs:
                        idxs.append(i)

                def _rank(s: str) -> int:
                    return {"sealed": 2, "suspect": 1, "unsealed": 0}.get(s, -1)

                best: SealResult | None = None
                for idx in idxs:
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
