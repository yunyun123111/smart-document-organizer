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

    def test_missing_field_placeholder(self):
        # 缺少编号 -> 未识别
        fields = {"date": "2026-08-20", "document_type": "销售合同", "company": "ABC"}
        name = self.svc.render("{日期}_{类型}_{公司}_{编号}", fields, ".pdf")
        assert name == f"2026-08-20_销售合同_ABC_{MISSING}.pdf"
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
        name = self.svc.render("{日期}_{类型}", {}, ".txt")
        assert name.startswith(MISSING)
        assert name.endswith(".txt")


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
