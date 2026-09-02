"""Phase 9 重命名服务测试：模板渲染/变量/缺失占位/非法字符/重名/超长。"""
from __future__ import annotations

from pathlib import Path

from backend.services.rename_service import MISSING, RenameService
from backend.utils.filename_utils import sanitize_filename, unique_filename


class TestRender:
    def setup_method(self):
        self.svc = RenameService()

    def test_default_template(self):
        fields = {"date": "2026-08-20", "document_type": "销售合同", "company": "ABC有限公司", "contract_no": "XS202608001"}
        name = self.svc.render("{日期}_{类型}_{公司}_{编号}", fields, ".pdf")
        assert name == "2026-08-20_销售合同_ABC有限公司_XS202608001.pdf"

    def test_english_variables(self):
        fields = {"date": "2026-08-20", "type": "发票", "company": "ABC", "invoice_no": "123456"}
        name = self.svc.render("{date}_{type}_{company}_{no}", fields, "pdf")
        assert name == "2026-08-20_发票_ABC_123456.pdf"

    def test_missing_field_skipped(self):
        # 缺少编号 -> 整段跳过（不再用"未识别"占位）
        fields = {"date": "2026-08-20", "document_type": "销售合同", "company": "ABC"}
        name = self.svc.render("{日期}_{类型}_{公司}_{编号}", fields, ".pdf")
        assert name == "2026-08-20_销售合同_ABC.pdf"
        assert "None" not in name and "null" not in name

    def test_illegal_chars_removed(self):
        fields = {"date": "2026-08-20", "document_type": "销售:合同/协议*?", "company": 'A<B>C|D', "contract_no": 'X\\S/1'}
        name = self.svc.render("{日期}_{类型}_{公司}_{编号}", fields, ".pdf")
        assert ":" not in name and "/" not in name and "*" not in name
        assert "?" not in name and "<" not in name and ">" not in name
        assert "|" not in name and "\\" not in name
        assert name.endswith(".pdf")

    def test_extra_separators_compressed(self):
        fields = {"date": "2026-08-20", "document_type": "发票", "company": "ABC", "invoice_no": "N1"}
        name = self.svc.render("{日期}_{类型}_{公司}_{编号}___{金额}", fields, ".pdf")
        # 空金额段被 MISSING 填充后压缩多余下划线
        assert "__" not in name

    def test_empty_template_falls_back(self):
        fields = {"date": "2026-08-20"}
        name = self.svc.render("", fields, ".pdf")
        assert name.endswith(".pdf")
        assert name

    def test_empty_fields(self):
        # 全部字段缺失 -> 文件名兜底为"未命名"
        name = self.svc.render("{日期}_{类型}", {}, ".txt")
        assert name == "未命名.txt"


class TestSanitize:
    def test_illegal_chars(self):
        assert sanitize_filename('a/b\\c:d*e?f"g<h>i|') == "abcdefghi"

    def test_whitespace_collapse(self):
        assert sanitize_filename("  销售   合同  ") == "销售 合同"

    def test_dots_stripped(self):
        assert sanitize_filename("...销售合同...") == "销售合同"

    def test_reserved_name(self):
        assert sanitize_filename("CON") == "_CON"

    def test_long_name_truncated(self):
        long = "甲" * 500
        result = sanitize_filename(long, max_length=50)
        assert len(result) == 50

    def test_empty(self):
        assert sanitize_filename("") == "未命名"
        assert sanitize_filename("   ") == "未命名"


class TestUniqueFilename:
    def test_no_conflict(self, tmp_path):
        p = unique_filename(tmp_path, "文件.pdf")
        assert p == tmp_path / "文件.pdf"

    def test_increment(self, tmp_path):
        (tmp_path / "文件.pdf").write_bytes(b"a")
        (tmp_path / "文件_001.pdf").write_bytes(b"b")
        p = unique_filename(tmp_path, "文件.pdf")
        assert p.name == "文件_002.pdf"

    def test_allow_overwrite(self, tmp_path):
        (tmp_path / "文件.pdf").write_bytes(b"a")
        p = unique_filename(tmp_path, "文件.pdf", allow_overwrite=True)
        assert p == tmp_path / "文件.pdf"

class TestDSL:
    """命名模板 DSL：IF 条件 / 缺失跳过 / 自定义函数。"""

    def setup_method(self):
        self.svc = RenameService()

    def test_if_equals(self):
        # {{IF 类型=发票}} -> 发票分支
        tpl = "{{IF 类型=发票}}{日期}_发票文件{{ELSE}}{日期}_普通文件{{END}}"
        name = self.svc.render(tpl, {"date": "2026-08-20", "document_type": "发票"}, ".pdf")
        assert name == "2026-08-20_发票文件.pdf"

    def test_if_else_fallback(self):
        tpl = "{{IF 类型=发票}}{日期}_发票{{ELSE}}{日期}_合同{{END}}"
        name = self.svc.render(tpl, {"date": "2026-08-20", "document_type": "销售合同"}, ".pdf")
        assert name == "2026-08-20_合同.pdf"

    def test_if_field_exists(self):
        # 无操作符：字段存在才渲染
        tpl = "{{IF 数量}}{船名}_有数量{{END}}{船名}"
        name = self.svc.render(tpl, {"vessel": "海兴168", "quantity": "8000"}, ".pdf")
        assert name == "海兴168_有数量海兴168.pdf"

    def test_if_missing_field_drops(self):
        tpl = "{{IF 数量}}{船名}_有数量{{END}}{船名}"
        name = self.svc.render(tpl, {"vessel": "海兴168"}, ".pdf")
        assert name == "海兴168.pdf"

    def test_if_not_equals(self):
        tpl = "{{IF 类型!=发票}}非发票{{ELSE}}发票{{END}}"
        assert self.svc.render(tpl, {"document_type": "结算单"}, ".pdf") == "非发票.pdf"
        assert self.svc.render(tpl, {"document_type": "发票"}, ".pdf") == "发票.pdf"

    def test_nested_if(self):
        tpl = "{{IF 类型=发票}}{{IF 金额}}发票_{金额:amount:int}{{END}}{{ELSE}}其他{{END}}"
        name = self.svc.render(tpl, {"document_type": "发票", "amount": "128500.00"}, ".pdf")
        assert name == "发票_128500.pdf"

    def test_date_compact(self):
        name = self.svc.render("{日期:date:compact}", {"date": "2026-08-14"}, ".pdf")
        assert name == "20260814.pdf"

    def test_date_cn(self):
        name = self.svc.render("{日期:date:cn}", {"date": "2026-08-14"}, ".pdf")
        assert name == "2026年08月14日.pdf"

    def test_amount_int(self):
        assert self.svc.render("{金额:amount:int}", {"amount": "128500.00"}, ".pdf") == "128500.pdf"
        assert self.svc.render("{金额:amount:round}", {"amount": "128500.60"}, ".pdf") == "128501.pdf"

    def test_company_short(self):
        assert self.svc.render("{公司:company:short}", {"company": "北京能源科技有限公司"}, ".pdf") == "北京能源.pdf"
        assert self.svc.render("{公司:company:short}", {"company": "ABC有限公司"}, ".pdf") == "ABC.pdf"

    def test_func_chain(self):
        # 链式函数：先取整再大写
        name = self.svc.render("{金额:amount:int|upper}", {"amount": "128500.00"}, ".pdf")
        assert name == "128500.pdf"

    def test_skip_missing_in_middle(self):
        # 中间字段缺失，两端字段保留、分隔符清理
        name = self.svc.render("{日期}_{船名}_{物料}_{类型}", {"date": "2026-08-14", "vessel": "海兴168", "document_type": "结算单"}, ".pdf")
        assert name == "2026-08-14_海兴168_结算单.pdf"

    def test_final_company_style(self):
        # 典型结算单模板 + 函数 + 缺失跳过
        tpl = "{日期:date:compact}_{船名}_{合同号}_{物料}_{类型}"
        name = self.svc.render(
            tpl,
            {"date": "2026-08-14", "vessel": "海兴168", "contract_no": "CG20260999", "document_type": "结算单"},
            ".pdf",
        )
        assert name == "20260814_海兴168_CG20260999_结算单.pdf"
        # 缺物料 -> 跳过
        name2 = self.svc.render(
            tpl,
            {"date": "2026-08-14", "vessel": "海兴168", "contract_no": "CG20260999", "document_type": "结算单"},
            ".pdf",
        )
        assert "未识别" not in name2
