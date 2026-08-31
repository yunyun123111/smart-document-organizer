"""OCR 服务（规格书第四十二节）。

设计：
- OCRProvider 抽象接口：未来可替换 PaddleOCR / Tesseract / 云 OCR
- 默认实现 RapidOCRProvider（PP-OCR 系模型，ONNX 运行时，pip 安装可靠）
- PaddleOCRProvider 预留，安装 paddlepaddle+paddleocr 后可切换
- OCRService 统一入口：业务代码只调用 OCRService，不直接接触具体 SDK
"""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger("services.ocr_service")


@dataclass
class OCRItem:
    """单个识别结果（一个文本框）。"""
    text: str
    confidence: float
    box: list = field(default_factory=list)  # 四点坐标 [[x,y],...]


@dataclass
class OCRResult:
    """一次 OCR 识别的完整结果。"""
    text: str
    confidence: float
    items: list[OCRItem] = field(default_factory=list)
    provider: str = ""


class OCRProvider(ABC):
    """OCR 提供方抽象。所有实现必须遵循此接口。"""

    name: str = "base"

    @abstractmethod
    def recognize(self, image: Image.Image) -> OCRResult:
        """对单张图像进行 OCR，返回统一 OCRResult。"""
        raise NotImplementedError


def _preprocess_image(image: Image.Image) -> Image.Image:
    """图像预处理（规格书第十九节：旋转校正 + 图像增强）。

    - EXIF 方向校正
    - 转 RGB
    - 灰度 + 对比度增强（提升印刷体识别率）
    """
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    gray = image.convert("L")
    enhancer = ImageEnhance.Contrast(gray)
    return enhancer.enhance(1.5)


class RapidOCRProvider(OCRProvider):
    """基于 rapidocr-onnxruntime（PP-OCR 系模型，ONNX 运行时）。

    模型在包内自带，无需联网下载，Windows/Python3.13 支持良好。
    """

    name = "rapidocr"
    _engine = None  # 延迟加载，模型只在首次识别时加载

    def _get_engine(self):
        if RapidOCRProvider._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            logger.info("加载 RapidOCR 模型（首次加载较慢）...")
            RapidOCRProvider._engine = RapidOCR()
            logger.info("RapidOCR 模型加载完成")
        return RapidOCRProvider._engine

    def recognize(self, image: Image.Image) -> OCRResult:
        engine = self._get_engine()
        image = _preprocess_image(image)

        # RapidOCR 接收 numpy 数组
        import numpy as np

        arr = np.array(image)
        result, _elapse = engine(arr)

        items: list[OCRItem] = []
        if result:
            for box, text, score in result:
                items.append(
                    OCRItem(
                        text=str(text),
                        confidence=float(score),
                        box=box.tolist() if hasattr(box, "tolist") else list(box),
                    )
                )

        full_text = "\n".join(item.text for item in items).strip()
        conf = (
            sum(item.confidence for item in items) / len(items)
            if items
            else 0.0
        )
        return OCRResult(text=full_text, confidence=conf, items=items, provider=self.name)


class PaddleOCRProvider(OCRProvider):
    """PaddleOCR 实现（规格书指定 V1 OCR）。

    需额外安装 paddlepaddle + paddleocr。未安装时调用会抛出明确的错误提示。
    通过 settings 或 Provider 工厂切换使用。
    """

    name = "paddleocr"
    _engine = None

    def _get_engine(self):
        if PaddleOCRProvider._engine is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as e:
                raise RuntimeError(
                    "PaddleOCR 未安装。请运行: pip install paddlepaddle paddleocr "
                    "或在设置中切换到 rapidocr。"
                ) from e
            logger.info("加载 PaddleOCR 模型...")
            PaddleOCRProvider._engine = PaddleOCR(lang="ch", use_angle_cls=True)
            logger.info("PaddleOCR 模型加载完成")
        return PaddleOCRProvider._engine

    def recognize(self, image: Image.Image) -> OCRResult:
        engine = self._get_engine()
        image = _preprocess_image(image)
        import numpy as np

        result = engine.predict(np.array(image))
        items: list[OCRItem] = []
        if result:
            for line in result:
                # paddleocr 3.x: 返回 dict 含 rec_texts / rec_scores
                texts = line.get("rec_texts") or []
                scores = line.get("rec_scores") or []
                for t, s in zip(texts, scores):
                    items.append(OCRItem(text=str(t), confidence=float(s)))
        full_text = "\n".join(item.text for item in items).strip()
        conf = sum(item.confidence for item in items) / len(items) if items else 0.0
        return OCRResult(text=full_text, confidence=conf, items=items, provider=self.name)


class OCRService:
    """OCR 统一入口。业务代码只允许通过本类调用 OCR。"""

    def __init__(self, provider: OCRProvider | None = None):
        self._provider = provider

    @property
    def provider(self) -> OCRProvider | None:
        # 每次检查开关：在系统设置里关闭 OCR 后立即停止识别（不沿用缓存实例）
        if not settings.OCR_ENABLED:
            return None
        if self._provider is None:
            # 默认使用 RapidOCR；可通过 .env / 设置切换 provider
            self._provider = RapidOCRProvider()
        return self._provider

    def recognize_image(self, image: Image.Image) -> OCRResult:
        """对 PIL 图像进行 OCR。未启用 OCR 时返回空结果。"""
        provider = self.provider
        if provider is None:
            logger.warning("OCR 未启用，跳过识别")
            return OCRResult(text="", confidence=0.0, provider="disabled")
        try:
            result = provider.recognize(image)
            logger.info(
                "OCR 完成：%d 项，文本 %d 字符，平均置信度 %.2f",
                len(result.items),
                len(result.text),
                result.confidence,
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("OCR 识别失败: %s", e)
            raise

    def recognize_image_bytes(self, data: bytes) -> OCRResult:
        """对图片字节流进行 OCR。"""
        image = Image.open(io.BytesIO(data))
        return self.recognize_image(image)

    def recognize_image_file(self, path: str | Path) -> OCRResult:
        """对图片文件进行 OCR。"""
        with Image.open(path) as img:
            return self.recognize_image(img)

    def recognize_pdf(self, pdf_path: str | Path) -> list[OCRResult]:
        """对扫描 PDF 的每一页渲染为图像后 OCR。

        返回按页顺序的 OCRResult 列表；无文本的页返回空结果。
        """
        import pymupdf as fitz  # PyMuPDF

        path = Path(pdf_path)
        doc = fitz.open(str(path))
        try:
            results: list[OCRResult] = []
            for page_index in range(doc.page_count):
                page = doc.load_page(page_index)
                # 150 DPI 渲染，兼顾清晰度与速度
                pix = page.get_pixmap(dpi=150)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                results.append(self.recognize_image(img))
            return results
        finally:
            doc.close()


# 全局单例
ocr_service = OCRService()
