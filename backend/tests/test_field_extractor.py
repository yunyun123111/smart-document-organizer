"""Phase 6 字段提取测试：公司/日期/合同编号/订单编号/发票号码/金额。"""
from __future__ import annotations

from backend.services.field_extractor import FieldExtractor


class TestFieldExtractor:
    def setup_method(self):
        self.fx = FieldExtractor()

    def _get(self, fields, name):
        return next((f for f in fields if f.name == name), None)

    def test_company_with_context(self):
        fields = self.fx.extract("甲方：北京科技有限公司 乙方：上海贸易有限公司")
        comp = self._get(fields, "company")
        assert comp is not None
        assert comp.value == "北京科技有限公司"

    def test_company_generic(self):
        fields = self.fx.extract("本报价单由深圳市华宇电子有限公司提供")
        comp = self._get(fields, "company")
        assert comp is not None
        assert "华宇" in comp.value

    def test_date_with_context(self):
        fields = self.fx.extract("签订日期：2026年08月20日")
        date = self._get(fields, "date")
        assert date is not None
        assert date.value == "2026-08-20"
        assert date.confidence > 0.9

    def test_date_generic(self):
        fields = self.fx.extract("订单日期 2026/8/5")
        date = self._get(fields, "date")
        assert date is not None
        assert date.value == "2026-08-05"

    def test_contract_no(self):
        fields = self.fx.extract("合同编号：XS202608001")
        f = self._get(fields, "contract_no")
        assert f is not None
        assert f.value == "XS202608001"

    def test_order_no(self):
        fields = self.fx.extract("订单号 PO2026001")
        f = self._get(fields, "order_no")
        assert f is not None
        assert f.value == "PO2026001"

    def test_invoice_no(self):
        fields = self.fx.extract("发票号码：123456789012345678")
        f = self._get(fields, "invoice_no")
        assert f is not None
        assert f.value == "123456789012345678"

    def test_amount_with_symbol(self):
        fields = self.fx.extract("金额：人民币 128,500.00 元")
        f = self._get(fields, "amount")
        assert f is not None
        assert f.value == "128500.00"

    def test_amount_with_context(self):
        fields = self.fx.extract("交易金额 50000元")
        f = self._get(fields, "amount")
        assert f is not None
        assert f.value == "50000"

    def test_amount_plain_with_yuan(self):
        fields = self.fx.extract("合同总价 8800 元")
        f = self._get(fields, "amount")
        assert f is not None
        assert f.value == "8800"

    def test_contract_full_scenario(self):
        text = (
            "销售合同\n"
            "合同编号：XS202608001\n"
            "甲方：ABC有限公司 乙方：DEF有限公司\n"
            "签订日期：2026年08月20日\n"
            "合同金额：人民币 128,500.00 元"
        )
        fields = self.fx.extract(text)
        names = {f.name for f in fields}
        assert {"company", "date", "contract_no", "amount"}.issubset(names)
        assert self._get(fields, "company").value == "ABC有限公司"
        assert self._get(fields, "date").value == "2026-08-20"
        assert self._get(fields, "amount").value == "128500.00"

    def test_no_fields(self):
        fields = self.fx.extract("这是一段完全无关的说明文字")
        assert fields == []

    def test_empty_text(self):
        assert self.fx.extract("") == []
        assert self.fx.extract(None) == []

    def test_to_dict(self):
        fields = self.fx.extract("发票号码：123456789012")
        f = fields[0]
        d = f.to_dict()
        assert d["name"] == "invoice_no"
        assert d["source"] == "RULE"
        assert d["value"] == "123456789012"
