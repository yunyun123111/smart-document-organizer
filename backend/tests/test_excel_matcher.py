"""Excel 登记表权威数据源匹配：列映射识别 / 规范化 / 主键+兜底匹配 / classifier 集成覆盖。"""
from __future__ import annotations

import json
from datetime import date, datetime

import openpyxl
import pytest

from backend.models import ExcelSheetConfig, ExcelSource
from backend.services.excel_matcher import (
    _LoadedSheet,
    _alnum,
    _norm_col,
    _norm_date,
    _norm_key,
    _norm_qty,
    detect_column_map,
    detect_key_column,
    excel_matcher,
)
from backend.services.field_extractor import ExtractedField

# 与用户真实台账一致的云创总合同表头
CLOUD_HEADERS = [
    "采购合同签订日期", "供应商", "港口", "船名", "物料名称", "单价", "数量", "采购合同金额",
    "不含税", "税额", "销售合同编号OA", "2.0编号", "采购合同编号", "2.0编号", "业务线",
    "销售合同签订日期", "采购商名称", "单价", "销售合同金额", "不含税", "税额", "采购量",
    "已收货转量", "采购货转进度", "销售量", "已开货转量", "销售货转进度",
]


# ---------- 规范化 ----------
class TestNormalize:
    def test_norm_key_fullwidth(self):
        assert _norm_key("HSYCXS（DD）-2026-0027") == "HSYCXS(DD)-2026-0027"
        assert _norm_key("sjwlxs(DD)-2026-yc0453") == "SJWLXS(DD)-2026-YC0453"

    def test_norm_key_space(self):
        assert _norm_key(" BRAVE SAILOR/勇敢水手 ") == "BRAVESAILOR/勇敢水手"

    def test_norm_date(self):
        assert _norm_date(datetime(2026, 2, 6)) == "2026-02-06"
        assert _norm_date(date(2026, 2, 6)) == "2026-02-06"
        assert _norm_date("2026/2/6") == "2026-02-06"
        assert _norm_date("2026-02-06 00:00:00") == "2026-02-06"

    def test_norm_qty(self):
        assert _norm_qty("10,000") == "10000"
        assert _norm_qty(7650000) == "7650000"

    def test_alnum(self):
        assert _alnum("YC-0453 a") == "YC0453A"

    def test_norm_col(self):
        assert _norm_col("销售合同编号OA") == "销售合同编号oa"
        assert _norm_col(" 船名 ") == "船名"


# ---------- 列映射识别 ----------
class TestColumnMap:
    def test_detect_cloud_headers(self):
        cmap = detect_column_map(CLOUD_HEADERS)
        assert cmap["contract_no"] == "销售合同编号OA"
        assert cmap["vessel"] == "船名"
        assert cmap["material"] == "物料名称"
        assert cmap["quantity"] == "数量"
        assert cmap["unit_price"] == "单价"
        assert cmap["amount"] == "采购合同金额"
        assert cmap["company"] == "供应商"
        assert cmap["seller"] == "采购商名称"
        assert cmap["date"] == "采购合同签订日期"

    def test_detect_key_column(self):
        assert detect_key_column(CLOUD_HEADERS) == "销售合同编号OA"

    def test_detect_settle_headers(self):
        settle = ["抬头", "供应商", "船名", "采购单价", "数量", "销售合同编号OA", "采购商名称",
                  "销售合同单价", "结算数量", "采购结算金额", "尾数", "尾票"]
        cmap = detect_column_map(settle)
        assert cmap["contract_no"] == "销售合同编号OA"
        assert cmap["unit_price"] == "采购单价"
        assert cmap["amount"] == "采购结算金额"


# ---------- _LoadedSheet 索引匹配 ----------
def _mk_sheet(rows: list[dict]) -> _LoadedSheet:
    cfg = ExcelSheetConfig()
    cfg.id = 0
    cfg.source_id = 0
    cfg.sheet_name = "test"
    cfg.enabled = True
    cfg.column_map = "{}"
    return _LoadedSheet(cfg, rows)


class TestLoadedSheet:
    def test_contract_exact(self):
        ls = _mk_sheet([
            {"contract_no": "HSYCXS（DD）-2026-0027", "vessel": "伦敦勇士号", "material": "PB粉"},
        ])
        assert ls.by_contract.get("HSYCXS(DD)-2026-0027")

    def test_contract_alt_columns(self):
        # 销售+采购合同号都进索引（__contracts）
        ls = _mk_sheet([
            {"contract_no": "HSYCXS（DD）-2026-0027", "__contracts": ["HSYCCG（DD）-2026-0023"]},
        ])
        assert ls.by_contract.get("HSYCXS(DD)-2026-0027")
        assert ls.by_contract.get("HSYCCG(DD)-2026-0023")

    def test_suffix_match(self):
        ls = _mk_sheet([
            {"contract_no": "SJWLXS（DD）-2026-YC0453", "vessel": "船1"},
            {"contract_no": "SJWLXS（DD）-2026-YC0458", "vessel": "船2"},
        ])
        suf = "YC0453"
        hits = [i for i in range(len(ls.rows)) if _norm_key(ls.rows[i]["contract_no"]).endswith(suf)]
        assert hits == [0]

    def test_vessel_date_index(self):
        ls = _mk_sheet([{"date": "2026-02-06", "vessel": "伦敦勇士号"}])
        assert ls.by_vessel_date.get(("2026-02-06", "伦敦勇士号"))

    def test_material_qty_index(self):
        ls = _mk_sheet([{"material": "PB粉", "quantity": "10000"}])
        assert ls.by_material_qty.get(("PB粉", "10000"))


# ---------- 端到端：生成临时 xlsx + 上传 + match ----------
def _write_tmp_xlsx(tmp_path, rows: list[dict]) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "云创总合同"
    ws.append(CLOUD_HEADERS)
    for r in rows:
        ws.append([
            r.get("date", ""), r.get("company", ""), "", r.get("vessel", ""),
            r.get("material", ""), r.get("unit_price", ""), r.get("quantity", ""),
            r.get("amount", ""), "", "", r.get("contract_no", ""), "", r.get("purchase_no", ""),
            "", "", "", r.get("seller", ""), "", "", "", "", "", "", "", "", "", "",
        ])
    # 第二个 sheet：无列映射（如草稿）
    ws2 = wb.create_sheet("草稿")
    ws2.append(["", ""])
    path = tmp_path / "台账.xlsx"
    wb.save(str(path))
    return str(path)


class TestEndToEnd:
    def _create_source(self, db, path):
        src = ExcelSource(name="台账.xlsx", file_path=path, enabled=True, total_sheets=2)
        db.add(src)
        db.flush()
        # 云创总合同
        cfg = ExcelSheetConfig(
            source_id=src.id, sheet_name="云创总合同", enabled=True,
            column_map=json.dumps({
                "contract_no": "销售合同编号OA", "vessel": "船名", "material": "物料名称",
                "quantity": "数量", "unit_price": "单价", "amount": "采购合同金额",
                "company": "供应商", "seller": "采购商名称", "date": "采购合同签订日期",
            }, ensure_ascii=False),
            key_column="销售合同编号OA",
        )
        db.add(cfg)
        # 草稿（禁用）
        db.add(ExcelSheetConfig(source_id=src.id, sheet_name="草稿", enabled=False, column_map="{}"))
        db.commit()
        excel_matcher.reload_source(db, src.id)
        return src.id

    def test_match_contract_and_override(self, db, tmp_path):
        path = _write_tmp_xlsx(tmp_path, [
            {"contract_no": "HSYCXS（DD）-2026-0027", "purchase_no": "HSYCCG（DD）-2026-0023",
             "vessel": "伦敦勇士号", "material": "PB粉", "quantity": "10000", "unit_price": "765",
             "amount": "7650000", "company": "日照恒盛德", "seller": "河津君鹏", "date": "2026-02-06"},
        ])
        sid = self._create_source(db, path)
        # 主键精确
        r = excel_matcher.match(db, {"contract_no": "HSYCXS（DD）-2026-0027"})
        assert r.matched
        assert r.sheet_name == "云创总合同"
        assert r.fields["vessel"] == "伦敦勇士号"
        assert r.fields["amount"] == "7650000"
        # 采购合同号（备选列）
        r2 = excel_matcher.match(db, {"contract_no": "HSYCCG（DD）-2026-0023"})
        assert r2.matched
        assert r2.fields["vessel"] == "伦敦勇士号"
        # 后缀
        r3 = excel_matcher.match(db, {"contract_no": "0027"})
        assert r3.matched
        assert r3.matched_field == "contract_no"
        # 未命中
        r4 = excel_matcher.match(db, {"contract_no": "NONEXIST-999"})
        assert not r4.matched
        # 禁用 sheet 不参与（草稿）
        assert "草稿" not in [s.cfg.sheet_name for s in excel_matcher._cache.get(sid, [])]

    def test_match_vessel_date_fallback(self, db, tmp_path):
        path = _write_tmp_xlsx(tmp_path, [
            {"contract_no": "HSYCXS（DD）-2026-0027", "vessel": "伦敦勇士号",
             "material": "PB粉", "quantity": "10000", "date": "2026-02-06"},
        ])
        self._create_source(db, path)
        r = excel_matcher.match(db, {"date": "2026-02-06", "vessel": "伦敦勇士号"})
        assert r.matched
        assert r.matched_field == "vessel_date"
        assert r.fields["contract_no"] == "HSYCXS（DD）-2026-0027"

    def test_match_material_qty_fallback(self, db, tmp_path):
        path = _write_tmp_xlsx(tmp_path, [
            {"contract_no": "HSYCXS（DD）-2026-0027", "material": "PB粉", "quantity": "10000",
             "date": "2026-02-06"},
        ])
        self._create_source(db, path)
        r = excel_matcher.match(db, {"material": "PB粉", "quantity": "10000"})
        assert r.matched
        assert r.matched_field == "material_quantity"

    def test_sheet_toggle_excludes_from_match(self, db, tmp_path):
        path = _write_tmp_xlsx(tmp_path, [
            {"contract_no": "HSYCXS（DD）-2026-0027", "vessel": "伦敦勇士号", "material": "PB粉"},
        ])
        sid = self._create_source(db, path)
        # 禁用主 sheet
        cfg = db.query(ExcelSheetConfig).filter_by(source_id=sid).first()
        cfg.enabled = False
        db.commit()
        excel_matcher.reload_source(db, sid)
        r = excel_matcher.match(db, {"contract_no": "HSYCXS（DD）-2026-0027"})
        assert not r.matched


# ---------- classifier 集成：Excel 覆盖 OCR ----------
class TestClassifierIntegration:
    def test_analysis_result_excel_match_field(self):
        from backend.services.classifier import AnalysisResult
        r = AnalysisResult(file_path="x.pdf")
        assert r.excel_match is None
        assert "excel_match" in r.to_dict()

    def test_excel_overrides_ocr_fields_in_analyze(self, seeded_db, tmp_path):
        """端到端：合同 PDF 识别出合同号 -> Excel 命中 -> 字段被台账覆盖、excel_match 落盘。"""
        import pymupdf as fitz
        from backend.services.classifier import ClassifierService

        # 1. 造一份销售合同 PDF（含合同编号）
        pdf = tmp_path / "合同.pdf"
        doc = fitz.open()
        page = doc.new_page()
        lines = [
            "销售合同",
            "合同编号：XS202608001",
            "甲方：ABC有限公司",
            "乙方：DEF有限公司",
            "签订日期：2026年8月20日",
        ]
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontname="china-s")
            y += 24
        doc.save(str(pdf))
        doc.close()

        # 2. 造一份 Excel 台账，含该合同号一行（船名/物料/供应商等权威字段）
        path = _write_tmp_xlsx(tmp_path, [
            {"contract_no": "XS202608001", "vessel": "伦敦勇士号", "material": "PB粉",
             "quantity": "10000", "unit_price": "765", "amount": "7650000",
             "company": "日照恒盛德", "seller": "河津君鹏", "date": "2026-08-20"},
        ])
        src = ExcelSource(name="台账.xlsx", file_path=path, enabled=True, total_sheets=2)
        seeded_db.add(src)
        seeded_db.flush()
        seeded_db.add(ExcelSheetConfig(
            source_id=src.id, sheet_name="云创总合同", enabled=True,
            column_map=json.dumps({
                "contract_no": "销售合同编号OA", "vessel": "船名", "material": "物料名称",
                "quantity": "数量", "unit_price": "单价", "amount": "采购合同金额",
                "company": "供应商", "seller": "采购商名称", "date": "采购合同签订日期",
            }, ensure_ascii=False),
            key_column="销售合同编号OA",
        ))
        seeded_db.add(ExcelSheetConfig(source_id=src.id, sheet_name="草稿", enabled=False, column_map="{}"))
        seeded_db.commit()
        excel_matcher.reload_source(seeded_db, src.id)

        # 3. 分析：合同号应命中 Excel，vessel/物料/供应商 被台账覆盖
        svc = ClassifierService(seeded_db)
        result = svc.analyze(pdf)

        assert not result.error, result.error
        assert result.excel_match is not None
        assert result.excel_match["matched"] is True
        # 覆盖字段：来源 EXCEL、置信度 1.0
        assert result.field_values["vessel"] == "伦敦勇士号"
        assert result.fields["vessel"] == ("伦敦勇士号", 1.0, "EXCEL")
        assert result.field_values["material"] == "PB粉"
        assert result.field_values["amount"] == "7650000"
