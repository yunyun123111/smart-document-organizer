# -*- coding: utf-8 -*-
"""查三个结算单的字段提取与 PDF 可读性。"""
import sys, io
sys.path.insert(0, r"C:\Users\gb\Desktop\文件管理工具\smart-document-organizer")
from backend.database import SessionLocal
from backend.models import Document, DocumentField

with SessionLocal() as db:
    docs = db.query(Document).filter(Document.status == "need_review").all()
    for d in docs:
        print(f"#{d.id} {d.original_filename} -> [{d.document_type}] conf={d.confidence}")
        fs = (
            db.query(DocumentField)
            .filter(DocumentField.document_id == d.id)
            .all()
        )
        if fs:
            for f in fs:
                print(f"   字段 {f.field_name} = {f.field_value}")
        else:
            print("   (无提取字段)")
        print()

# 尝试读取 PDF 文本内容
print("=" * 50)
print("PDF 文本解析测试：")
for name in ["夜空PB粉预结算单0433.pdf", "河北王朝PB粉预结算单.pdf", "维克托克粉预结算单.pdf"]:
    path = r"C:\Users\gb\Desktop\文件管理工具\smart-document-organizer\data\inbox" + "\\" + name
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        total_text = ""
        for page in doc:
            total_text += page.get_text()
        print(f"--- {name} ---")
        print(f"页数={len(doc)}, 文本长度={len(total_text.strip())}")
        print(f"文本预览: {total_text.strip()[:300]!r}")
    except Exception as e:
        print(f"--- {name} --- 读取失败: {e}")
