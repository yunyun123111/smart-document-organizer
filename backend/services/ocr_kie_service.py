"""视觉 KIE 兜底服务 —— 让 Xiaomi-OCR-0 直读单据原图输出 JSON 字段。

用途：RapidOCR + 正则链路在扫描件上常丢关键字段（金额、合同号、发票号），
这里把字段清单作为 schema 交给视觉模型，让它一次性输出 JSON，作为补抽。

与 ``XiaomiOCRProvider`` 的关系：共用同一个 OpenAI 兼容端点与图片压缩逻辑，
区别是 prompt 要求结构化 JSON，而不是整页文本。
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger("services.ocr_kie")

# 字段 schema：逻辑字段名（与 field_extractor / 文件名模板一致）-> 抽取说明
FIELD_SCHEMA: dict[str, str] = {
    "company": "单据上的对方公司/客户全称",
    "seller": "销售方或供货方全称",
    "date": "单据日期，统一写成 YYYY-MM-DD",
    "contract_no": "合同编号，原样保留括号与横线（如 HSYCXS(DD)-2026-0236）",
    "invoice_no": "发票号码，纯数字",
    "amount": (
        "单据上明确标注为「最终结算金额」或「价税合计」的那一个数字。"
        "不要取合同单价、结算单价，也不要取「已收货款/已付款」"
    ),
    "quantity": "货物数量（单位吨），不是单价",
    "vessel": "船名",
    "material": "货物/物料名称（如 PB粉、铁矿粉）",
    "summary": "款项性质或摘要（如 货款、保证金、利息）",
}

# KIE 可覆盖既有值的字段：编号/金额类正则最易错位，视觉直读更可信
_OVERWRITE_FIELDS = {"amount", "contract_no", "invoice_no"}
# 默认只抽"视觉模型稳定"的核心字段。quantity / material / summary 类在实测中
# 容易错位（把单价读成数量、把公司名片段读成物料），默认不请求，避免污染字段。
CORE_FIELDS: tuple[str, ...] = (
    "company", "seller", "date", "contract_no", "invoice_no", "amount",
)
# 没有任何模板时，缺金额即认为该单据需要兜底
_DEFAULT_TRIGGER = ("amount",)


@dataclass
class KIEResult:
    """一次 KIE 抽取结果。"""
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""
    elapsed: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.fields) and not self.error


def parse_json_object(raw: str) -> dict:
    """从模型输出里解析 JSON 对象（容忍 ```json 包裹与前后废话）。"""
    if not raw:
        return {}
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _normalize(key: str, value) -> str:
    """把模型返回的值清洗成项目字段口径。"""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "unknown", "无", "未提供", "n/a"):
        return ""
    if key == "amount":
        s = s.replace("¥", "").replace("￥", "").replace("元", "").strip()
    elif key == "quantity":
        s = s.replace("吨", "").strip()
    elif key == "date":
        m = re.match(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
        if m:
            s = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


class OCRKIEService:
    """按 schema 调视觉模型做字段抽取。"""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: int | None = None):
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    # ---- 配置延迟读取：设置页改动后无需重建实例 ----
    @property
    def base_url(self) -> str:
        return (self._base_url or settings.OCR_BASE_URL).rstrip("/")

    @property
    def model(self) -> str:
        return self._model or settings.OCR_MODEL

    @property
    def timeout(self) -> int:
        return self._timeout or settings.OCR_TIMEOUT

    def reset(self) -> None:
        """清掉显式参数，回到读 settings（设置页切换端点后调用）。"""
        self._base_url = None
        self._model = None
        self._timeout = None

    def build_prompt(self, fields: list[str] | None = None,
                     document_type: str | None = None) -> str:
        """按字段清单生成 JSON schema prompt。

        默认给全字段：实测只给 6 个核心字段时模型会丢表格语境，把单价当结算金额；
        字段清单完整反而更稳。最终用哪些字段由合并层（CORE_FIELDS）决定。
        """
        keys = fields or list(FIELD_SCHEMA.keys())
        lines = [
            "从这张单据中抽取字段，只输出一个 JSON 对象。",
            "不要输出 Markdown 代码块、不要解释、不要补充未要求的字段。",
            "没有出现的字段填 null。金额和数量只保留数值（可带千分位逗号，不要写单位）。",
        ]
        if document_type:
            lines.append(f"单据类型：{document_type}")
        lines.append("字段说明：")
        for k in keys:
            desc = FIELD_SCHEMA.get(k, k)
            lines.append(f"- {k}: {desc}")
        lines.append(f"输出格式示例：{json.dumps({k: '值或 null' for k in keys}, ensure_ascii=False)}")
        return "\n".join(lines)

    def _encode_image(self, image: Image.Image) -> tuple[str, str]:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
        if image.mode != "RGB":
            image = image.convert("RGB")
        edge = max(image.size)
        limit = settings.OCR_MAX_IMAGE_EDGE
        if limit and edge > limit:
            ratio = limit / edge
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
            )
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        import base64

        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/png"

    def extract(self, image: Image.Image, fields: list[str] | None = None,
                document_type: str | None = None) -> KIEResult:
        """对单页图像做 KIE。失败返回带 error 的结果，不抛异常（兜底不能中断主流程）。"""
        t0 = time.time()
        try:
            b64, mime = self._encode_image(image)
            payload = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.build_prompt(fields, document_type)},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }],
                "max_tokens": 1024,
                "temperature": 0,
                "stream": False,
            }
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
            obj = parse_json_object(raw)
            out: dict[str, str] = {}
            for k, v in obj.items():
                if k not in FIELD_SCHEMA:
                    continue
                norm = _normalize(k, v)
                if norm:
                    out[k] = norm
            elapsed = time.time() - t0
            logger.info("KIE 抽取完成：%s（%.1fs）", list(out.keys()), elapsed)
            return KIEResult(fields=out, raw=raw, elapsed=elapsed)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:150]
            msg = f"HTTP {e.code}: {detail}"
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
        elapsed = time.time() - t0
        logger.warning("KIE 抽取失败（已跳过，不影响识别）：%s", msg)
        return KIEResult(raw="", elapsed=elapsed, error=msg)

    def extract_from_file(self, path: str | Path) -> KIEResult:
        """图片直接读；PDF 渲染首页（单据的关键字段都在首页）。"""
        p = Path(path)
        if p.suffix.lower() == ".pdf":
            import pymupdf as fitz

            doc = fitz.open(str(p))
            try:
                if doc.page_count == 0:
                    return KIEResult(error="空 PDF")
                pix = doc.load_page(0).get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                return self.extract(img)
            finally:
                doc.close()
        with Image.open(p) as img:
            return self.extract(img)


# 全局单例
ocr_kie_service = OCRKIEService()
