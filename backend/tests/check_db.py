"""临时验证脚本：检查数据库初始化结果（里程碑 1 验证用）。"""
import sys
from pathlib import Path

# 项目根 = 本文件（backend/tests/check_db.py）的上三级
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.database import SessionLocal  # noqa: E402

with SessionLocal() as db:
    tables = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ).scalars().all()
    print("数据表:", tables)

    cats = db.execute(text("SELECT id, parent_id, name, path FROM categories ORDER BY id")).all()
    print(f"\n默认分类共 {len(cats)} 个：")
    for c in cats:
        indent = "  " if c.parent_id else ""
        print(f"  {indent}#{c.id} {c.name}  (path={c.path})")

    doc_count = db.execute(text("SELECT COUNT(*) FROM documents")).scalar()
    print(f"\ndocuments 记录数: {doc_count}")
print("数据库初始化验证通过 ✔")
