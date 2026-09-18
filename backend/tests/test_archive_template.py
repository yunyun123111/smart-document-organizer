# -*- coding: utf-8 -*-
"""归档目录结构模板测试：category/year/month 默认不变 + 自定义模板解析。"""
from pathlib import Path

import pytest

from backend.config import settings
from backend.models import Document, DocumentField
from backend.services.archive_service import ArchiveService


@pytest.fixture
def tpl_env(monkeypatch):
    """隔离归档模板修改：防写盘 + 用例后恢复旧模板。"""
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    old = settings.ARCHIVE_TEMPLATE
    yield
    settings.update(archive_template=old)


def _set_template(tpl: str) -> None:
    settings.update(archive_template=tpl)


def _svc(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    return ArchiveService(None, document_root=root), root  # type: ignore[arg-type]


def test_default_template_year_month(tmp_path, tpl_env):
    """默认模板 category/year/month：行为与历史一致。"""
    svc, root = _svc(tmp_path)
    d = svc.build_target_dir("合同/销售合同", "2026-08-20")
    assert d == root / "合同/销售合同/2026/08"


def test_default_template_bad_date(tmp_path, tpl_env):
    """日期非法：年/月段跳过，只留分类。"""
    svc, root = _svc(tmp_path)
    d = svc.build_target_dir("合同", "not-a-date")
    assert d == root / "合同"


def test_custom_customer_contract(tmp_path, tpl_env):
    """自定义模板：分类/客户名/合同编号。"""
    _set_template("category/customer/contract_no")
    svc, root = _svc(tmp_path)
    fields = {"customer": "ABC公司", "contract_no": "XS202608001"}
    d = svc.build_target_dir("合同/销售合同", None, fields)
    assert d == root / "合同/销售合同/ABC公司/XS202608001"


def test_custom_missing_field_skips(tmp_path, tpl_env):
    """字段缺失：该段自动跳过，不留空目录。"""
    _set_template("category/customer/contract_no")
    svc, root = _svc(tmp_path)
    fields = {"contract_no": "XS202608001"}  # 无 customer
    d = svc.build_target_dir("合同", None, fields)
    assert d == root / "合同/XS202608001"


def test_custom_ship_project_date(tmp_path, tpl_env):
    """模板：分类/船名/日期。"""
    _set_template("category/ship_name/date")
    svc, root = _svc(tmp_path)
    fields = {"ship_name": "华航168", "date": "2026-08-14"}
    d = svc.build_target_dir("结算单", None, fields)
    assert d == root / "结算单/华航168/2026-08-14"


def test_custom_unsafe_segment_sanitized(tmp_path, tpl_env):
    """字段值含非法字符：清洗后用于目录名，不产生越界路径。"""
    _set_template("category/customer")
    svc, root = _svc(tmp_path)
    fields = {"customer": "../越界/名称:测试"}
    d = svc.build_target_dir("合同", None, fields)
    rel = str(d.relative_to(root))
    assert ".." not in rel  # 未越界
    assert ":" not in rel and "/" not in rel.replace("合同/", "")  # 非法字符已清洗
    assert d == root / "合同" / "越界名称测试"


def test_unknown_segment_skipped(tmp_path, tpl_env):
    """未知模板段：跳过不报错。"""
    _set_template("category/unknown_seg/year")
    svc, root = _svc(tmp_path)
    d = svc.build_target_dir("合同", "2026-08-20", {})
    assert d == root / "合同/2026"


def test_traversal_still_blocked(tmp_path, tpl_env):
    """分类路径穿越防护不受模板影响。"""
    svc, _ = _svc(tmp_path)
    try:
        svc.build_target_dir("../../etc", "2026-08-20")
        assert False, "应拒绝越界分类路径"
    except ValueError:
        pass


def test_archive_reads_fields_from_db(tmp_path, db, monkeypatch, tpl_env):
    """真实归档：模板字段段从 document_fields 读取，生成 分类/合同号/船名 目录。"""
    _set_template("category/contract_no/ship_name")
    doc = Document(
        original_filename="test.pdf",
        original_path="",
        file_type="pdf",
        file_size=10,
        status="pending",
    )
    db.add(doc)
    db.flush()
    db.add_all(
        [
            DocumentField(document_id=doc.id, field_name="contract_no", field_value="XS202608001"),
            DocumentField(document_id=doc.id, field_name="ship_name", field_value="华航168"),
        ]
    )
    db.commit()

    svc = ArchiveService(db, document_root=tmp_path / "docs")
    src = tmp_path / "inbox.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    result = svc.archive(src, "合同/销售合同", "XS202608001.pdf", document_id=doc.id)
    assert result.success, result.error
    assert result.document_path is not None
    rel = result.document_path.relative_to(tmp_path / "docs")
    assert rel.as_posix() == "合同/销售合同/XS202608001/华航168/XS202608001.pdf"
