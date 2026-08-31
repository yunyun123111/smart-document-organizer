"""重命名服务（规格书 Phase 9 / 第三十节）。

- 支持模板变量：{日期} {类型} {公司} {编号} {金额} 等（兼容中英文变量名）
- 字段缺失时用"未识别"，绝不生成 null/None/undefined
- 非法字符 / 空字段 / 重名 / 超长 全部处理
"""
from __future__ import annotations

from pathlib import Path

from backend.utils.filename_utils import safe_filename, unique_filename
from backend.utils.logger import get_logger

logger = get_logger("services.rename_service")

# 模板变量别名表：模板中的变量名 -> 可接受的字段 key
VARIABLE_ALIASES: dict[str, list[str]] = {
    "日期": ["date", "日期"],
    "类型": ["document_type", "type", "类型"],
    "公司": ["company", "公司"],
    "编号": ["contract_no", "order_no", "invoice_no", "no", "编号"],
    "no": ["contract_no", "order_no", "invoice_no", "no", "编号"],
    "合同编号": ["contract_no", "合同编号"],
    "合同号": ["contract_no", "合同编号", "合同号"],
    "发票号码": ["invoice_no", "发票号码"],
    "订单编号": ["order_no", "订单编号"],
    "金额": ["amount", "金额"],
    "付款方": ["payer", "付款方"],
    "收款方": ["payee", "收款方"],
    "船名": ["vessel", "船名"],
    "物料": ["material", "物料名称", "物料", "品名"],
    "数量": ["quantity", "数量"],
}

# 缺失字段占位
MISSING = "未识别"

# 默认模板（规格书第五十一节）
DEFAULT_TEMPLATE = "{日期}_{类型}_{公司}_{编号}"


class RenameService:
    """重命名服务：渲染模板 + 文件名安全 + 重名处理。"""

    def render(self, template: str, fields: dict, extension: str = "") -> str:
        """按模板渲染文件名。

        fields: 字段字典，key 为英文（date/company/...）或中文
        extension: 文件扩展名（如 .pdf，含或不含点均可）
        返回安全完整文件名。
        """
        fields = fields or {}
        result = template or DEFAULT_TEMPLATE

        # 替换 {变量}
        import re

        def _replace(match: re.Match) -> str:
            var = match.group(1)
            value = self._lookup_value(var, fields)
            if value:
                return value
            # 缺字段 fallback：数量 -> 金额 -> 未识别（保持文件名成形）
            for fb in ("quantity", "amount"):
                v = fields.get(fb)
                if v not in (None, "", "null", "None", "undefined"):
                    return str(v).strip()
            return MISSING

        result = re.sub(r"\{([^{}]+)\}", _replace, result)
        # 清理多余分隔符（连续下划线/空格/横线压缩）
        result = re.sub(r"(_+\s*_+|_{2,}|\s{2,})", "_", result).strip("_ ")
        return safe_filename(result, extension)

    def _lookup_value(self, var: str, fields: dict) -> str:
        var = var.strip()
        aliases = VARIABLE_ALIASES.get(var, [var])
        for key in aliases:
            val = fields.get(key)
            if val not in (None, "", "null", "None", "undefined"):
                return str(val).strip()
        # 直接键命中
        if var in fields and fields[var] not in (None, "", "null", "None", "undefined"):
            return str(fields[var]).strip()
        return ""

    def build_unique_path(self, target_dir: Path, filename: str, allow_overwrite: bool = False) -> Path:
        """在目标目录生成不冲突的完整路径。"""
        return unique_filename(target_dir, filename, allow_overwrite=allow_overwrite)


# 全局单例
rename_service = RenameService()
