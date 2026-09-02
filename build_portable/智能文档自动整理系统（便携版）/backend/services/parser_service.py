"""文档解析统一接口与解析服务。

核心设计（规格书第十七节）：
- 所有解析器实现 BaseParser 接口（supports / parse）
- 返回统一的 ParsedDocument 对象，后续业务逻辑只依赖该对象
- 不能让 PDF、Word、Excel 的数据结构完全不同地传递到后面
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from backend.utils.file_utils import get_file_type
from backend.utils.logger import get_logger

logger = get_logger("services.parser_service")

# 文本来源
SOURCE_TEXT = "TEXT"
SOURCE_OCR = "OCR"

# PDF 扫描件判定：平均每页文本字符数低于该值视为扫描件，需要 OCR
PDF_TEXT_THRESHOLD_PER_PAGE = 50

# 已登记建档但 V1 没有解析器的类型 -> 给用户的可操作提示
_NO_PARSER_HINTS: dict[str, str] = {
    "doc": "V1 只解析 .docx，请先另存为 .docx 后重新整理",
    "xls": "V1 只解析 .xlsx，请先另存为 .xlsx 后重新整理",
}


@dataclass
class ParsedDocument:
    """所有解析器的统一返回对象。

    text:   提取到的文本（清洗前的原始文本）
    metadata: 通用元数据（页数、sheet 数、图像尺寸等）
    pages:   页数（无分页概念的类型为 None）
    tables:  结构化表格列表，每项为 list[list[str]]
    images:  图像信息列表
    needs_ocr: 是否需要 OCR 补充（扫描 PDF / 纯图片）
    source:  文本来源（TEXT / OCR）
    """

    text: str = ""
    metadata: dict = field(default_factory=dict)
    pages: int | None = None
    tables: list = field(default_factory=list)
    images: list = field(default_factory=list)
    needs_ocr: bool = False
    source: str = SOURCE_TEXT

    @property
    def text_length(self) -> int:
        return len(self.text)


class BaseParser(ABC):
    """解析器基类。子类必须实现 supports 与 parse。"""

    #: 该解析器支持的扩展名集合（小写，不含点）
    file_types: tuple[str, ...] = ()

    def supports(self, file_type: str) -> bool:
        return file_type.lower() in self.file_types

    @abstractmethod
    def parse(self, file_path: str | Path) -> ParsedDocument:
        """解析文件，返回统一 ParsedDocument。"""
        raise NotImplementedError


class ParserService:
    """解析服务：按文件类型分发到具体解析器。"""

    def __init__(self, parsers: list[BaseParser] | None = None):
        # 延迟导入，避免模块循环依赖
        from backend.services.excel_parser import ExcelParser
        from backend.services.image_parser import ImageParser
        from backend.services.pdf_parser import PdfParser
        from backend.services.text_parser import CsvParser, TxtParser
        from backend.services.word_parser import WordParser

        self._parsers: list[BaseParser] = parsers or [
            PdfParser(),
            ImageParser(),
            WordParser(),
            ExcelParser(),
            TxtParser(),
            CsvParser(),
        ]

    def supports(self, file_type: str) -> bool:
        return any(p.supports(file_type) for p in self._parsers)

    def get_parser(self, file_type: str) -> BaseParser | None:
        for p in self._parsers:
            if p.supports(file_type):
                return p
        return None

    def parse_file(self, path: str | Path) -> ParsedDocument:
        """解析文件。类型不支持时抛出 ValueError。"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {p}")

        file_type = get_file_type(p)
        parser = self.get_parser(file_type)
        if parser is None:
            # 已登记建档但 V1 没有解析器的类型（如 doc / xls），给出可操作提示
            hint = _NO_PARSER_HINTS.get(file_type)
            if hint:
                raise ValueError(f"{p.suffix} 文件暂不支持解析：{hint}")
            raise ValueError(f"不支持的文件类型: {p.suffix or '无扩展名'}")

        logger.info("开始解析 %s (type=%s, parser=%s)", p.name, file_type, type(parser).__name__)
        result = parser.parse(p)
        result.metadata.setdefault("file_type", file_type)
        result.metadata.setdefault("file_name", p.name)
        return result


# 单例，供全局使用
parser_service = ParserService()
