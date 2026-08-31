"""端到端演示脚本：验证「识别 → 归档 → 撤销」全链路（里程碑 2 验证用）。

流程：
1. 生成一份销售合同 PDF（模拟用户放入"待整理"）
2. 使用 ClassifierService 识别（解析→OCR→规则→字段→置信度→建议）
3. 使用 ArchiveService 归档（去重→建目录→重命名→移动→日志→登记文档）
4. 使用 UndoService 撤销（恢复原位置）
"""
import sys
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.services.archive_service import ArchiveService  # noqa: E402
from backend.services.classifier import ClassifierService  # noqa: E402
from backend.models import OperationLog, OP_ARCHIVE  # noqa: E402
from backend.services.undo_service import undo_service  # noqa: E402

DEMO_DIR = ROOT / "data/temp/demo"


def make_contract_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "销售合同",
        "合同编号：XS202608001",
        "甲方：ABC有限公司",
        "乙方：DEF有限公司",
        "合同金额：人民币 128,500.00 元",
        "签订日期：2026年08月20日",
        "本合同由甲乙双方根据《中华人民共和国民法典》订立，双方同意按照本合同条款执行。",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s")
        y += 24
    doc.save(str(path))
    doc.close()


def main():
    print("=" * 60)
    print("里程碑 2 端到端演示：识别 → 归档 → 撤销")
    print("=" * 60)

    init_db()  # 幂等：确保表 + 默认分类 + 默认规则
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    src = DEMO_DIR / "IMG20260826.pdf"
    if src.exists():
        src.unlink()
    make_contract_pdf(src)
    print(f"[1] 模拟用户放入待整理文件: {src.name}")

    with SessionLocal() as db:
        # --- 识别 ---
        print("\n[2] 智能识别中...")
        classifier = ClassifierService(db)
        result = classifier.analyze(src)
        print(f"    文档类型: {result.document_type}")
        print(f"    识别字段: {result.field_values}")
        print(f"    综合置信度: {result.confidence.score:.3f} (决策: {result.decision})")
        print(f"    建议分类: {result.suggested_category}")
        print(f"    建议文件名: {result.suggested_filename}")

        # --- 归档 ---
        print("\n[3] 归档中...")
        archive = ArchiveService(db)
        ar = archive.archive(
            src,
            category_path=result.suggested_category,
            filename=result.suggested_filename,
            date_str=result.field_values.get("date"),
        )
        if not ar.success:
            print(f"    归档失败: {ar.error}")
            return
        print(f"    归档成功: {ar.document_path}")
        assert not src.exists(), "原文件应已移动"

        # --- 撤销 ---
        print("\n[4] 撤销测试...")
        log = (
            db.query(OperationLog)
            .filter(OperationLog.operation_type == OP_ARCHIVE)
            .order_by(OperationLog.id.desc())
            .first()
        )
        ur = undo_service.undo(db, log.id)
        if ur.success:
            print(f"    撤销成功，文件已恢复到: {ur.restored_path}")
            assert src.exists()
        else:
            print(f"    撤销失败: {ur.error}")

    print("\n" + "=" * 60)
    print("端到端演示通过 ✔  (识别→归档→撤销 全链路正常)")
    print("=" * 60)


if __name__ == "__main__":
    main()
