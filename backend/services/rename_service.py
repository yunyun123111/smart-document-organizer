"""重命名服务 v2 —— 命名模板 DSL（规格书 Phase 9 升级）。

在原 `{变量}` 占位符基础上新增：
1. IF 条件逻辑：`{{IF 类型=发票}}...{{ELSE}}...{{END}}`
   - 条件支持：字段存在性 / 字段==值 / 字段=值 / 字段!=值 / 字段<>值
   - 字段可用中文别名（类型=document_type 等）
2. 字段缺失自动跳过：缺失字段整段删除（含连接分隔符），不留"未识别"占位
3. 自定义函数：`{字段:函数}` 或 `{字段:函数1|函数2}` 链式
   - date:compact（2026-08-14→20260814） / date:cn（2026年08月14日）
   - amount:int（128500.00→128500） / amount:round（四舍五入取整）
   - company:short（XX有限公司→XX）
   - upper / lower

向后兼容：`{变量}`、中英文变量名、缺字段清理全部保留。
"""
from __future__ import annotations

import re
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
    "品种": ["material", "品种", "物料"],
    "数量": ["quantity", "数量"],
    "合同尾号": ["contract_suffix", "合同尾号"],
    "销售方": ["seller", "company", "销售方", "公司"],
    "购买方": ["buyer", "company", "购买方", "公司"],
}

# 兼容保留（部分外部引用）；新 DSL 渲染默认缺失跳过，不再填充该占位
MISSING = "未识别"

# 默认模板（规格书第五十一节）
DEFAULT_TEMPLATE = "{日期}_{类型}_{公司}_{编号}"

# 公司名缩写：去掉常见后缀
_COMPANY_SUFFIX_RE = re.compile(
    r"(股份有限公司|有限责任公司|集团有限公司|科技有限公司|有限公司|公司|集团|工厂|商行|事务所|合作社|医院|学校)$"
)

# IF 条件块：{{IF 条件}}...{{ELSE}}...{{END}}（支持嵌套，迭代消解）
_IF_RE = re.compile(r"{{\s*IF\s+(.+?)\s*}}(.*?)(?:{{\s*ELSE\s*}}(.*?))?{{\s*END\s*}}", re.S)
_COND_OP_RE = re.compile(r"^(.+?)\s*(==|=|!=|<>)\s*(.+?)$")


class RenameService:
    """重命名服务：DSL 渲染 + 文件名安全 + 重名处理。"""

    def render(self, template: str, fields: dict, extension: str = "") -> str:
        """按模板 DSL 渲染文件名。

        fields: 字段字典，key 为英文（date/company/...）或中文
        extension: 文件扩展名（如 .pdf，含或不含点均可）
        返回安全完整文件名。
        """
        fields = fields or {}
        text = template or DEFAULT_TEMPLATE
        # ① IF 条件逻辑
        text = self._eval_conditionals(text, fields)
        # ② 变量 + 自定义函数 + 缺失跳过
        text = self._replace_vars(text, fields)
        # ③ 清理连续分隔符与首尾
        text = re.sub(r"(_+\s*_+|_{2,}|\s{2,})", "_", text).strip("_ ")
        return safe_filename(text, extension)

    # ---------- ① IF 条件逻辑 ----------
    def _eval_conditionals(self, text: str, fields: dict) -> str:
        """解析 {{IF 条件}} 块；支持嵌套（迭代直到稳定）。"""
        for _ in range(20):
            new = _IF_RE.sub(
                lambda m: (m.group(2) if self._eval_cond(m.group(1), fields) else (m.group(3) or "")),
                text,
            )
            if new == text:
                break
            text = new
        return text

    def _eval_cond(self, cond: str, fields: dict) -> bool:
        cond = cond.strip()
        m = _COND_OP_RE.match(cond)
        if m:
            key, op, val = m.group(1).strip(), m.group(2), m.group(3).strip()
            fv = self._lookup_value(key, fields)
            if op in ("==", "="):
                return fv == val
            return fv != val
        # 无操作符：字段存在性判断
        return bool(self._lookup_value(cond, fields))

    # ---------- ② 变量 / 函数 / 缺失跳过 ----------
    def _replace_vars(self, text: str, fields: dict) -> str:
        def _rep(match: re.Match) -> str:
            spec = match.group(1).strip()
            # 先按 | 切函数链，再对首段切 :（字段:函数）
            segs = [s.strip() for s in spec.split("|")]
            first = segs[0]
            if ":" in first:
                nm, _, f0 = first.partition(":")
                funcs = [f0] + segs[1:]
            else:
                nm = first
                funcs = segs[1:]
            value = self._lookup_value(nm, fields)
            if value in (None, ""):
                return ""  # 字段缺失自动跳过
            for fn in funcs:
                value = self._apply_func(fn, value)
            return value

        return re.sub(r"\{([^{}]+)\}", _rep, text)

    def _apply_func(self, fn: str, value: str) -> str:
        """自定义函数：日期格式化 / 金额取整 / 公司名缩写 等。"""
        value = str(value)
        if fn in ("compact", "date:compact"):
            return re.sub(r"[-/.]", "", value)
        if fn in ("cn", "date:cn"):
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", value)
            if m:
                return f"{m.group(1)}年{int(m.group(2)):02d}月{int(m.group(3)):02d}日"
            return value
        if fn in ("int", "amount:int"):
            m = re.search(r"\d+(?:\.\d+)?", value)
            return str(int(float(m.group(0)))) if m else value
        if fn in ("round", "amount:round"):
            m = re.search(r"\d+(?:\.\d+)?", value)
            return str(round(float(m.group(0)))) if m else value
        if fn in ("short", "company:short"):
            return _COMPANY_SUFFIX_RE.sub("", value) or value
        if fn == "upper":
            return value.upper()
        if fn == "lower":
            return value.lower()
        logger.warning("未识别的模板函数: %s（原样输出）", fn)
        return value

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
