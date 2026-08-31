"""真实数据库端到端验证：调用本地 8000 API 走通完整业务流。

流程：写测试文件到 inbox → 批量整理 → 待审核 → 确认归档 → 撤销 → 清理。
"""
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api"
ROOT = Path(r"C:\Users\gb\Desktop\文件管理工具\smart-document-organizer")


def main():
    client = httpx.Client(timeout=30)
    # 1. 准备测试文件（销售合同 txt）
    test_file = Path(ROOT / "data/inbox/e2e_合同测试.txt")
    test_file.write_text(
        "销售合同\n合同编号：E2E2026082601\n甲方：测试公司\n"
        "合同金额：人民币 66,600.00 元\n签订日期：2026年08月26日\n"
        "本合同为端到端验证使用。\n",
        encoding="utf-8",
    )
    print("[1] 测试文件已放入收件箱:", test_file.name)

    # 2. 批量整理
    r = client.post(f"{BASE}/processing/start", json={})
    r.raise_for_status()
    job_id = r.json()["id"]
    print(f"[2] 任务 #{job_id} 已启动，等待完成...")

    import time

    for _ in range(60):
        job = client.get(f"{BASE}/processing/{job_id}").json()
        if job["status"] in ("completed", "cancelled", "failed"):
            break
        time.sleep(0.3)
    print(f"    任务状态: {job['status']} | 处理 {job['processed_files']}/{job['total_files']} "
          f"| 成功 {job['success_count']} | 待审核 {job['review_count']}")

    # 3. 待审核列表
    items = client.get(f"{BASE}/review").json()
    assert items, "应有待审核项"
    doc_id = items[0]["id"]
    detail = client.get(f"{BASE}/review/{doc_id}").json()
    print(f"[3] 待审核文档: {detail['document']['original_filename']} -> {detail['document']['document_type']}")
    print(f"    建议分类: {detail['suggested_category']} | 建议文件名: {detail['suggested_filename']}")

    # 4. 确认归档
    r = client.post(f"{BASE}/review/{doc_id}/approve", json={})
    r.raise_for_status()
    doc = client.get(f"{BASE}/documents/{doc_id}").json()
    print(f"[4] 已归档 -> {doc['current_path']}")
    assert doc["status"] == "archived"
    assert Path(doc["current_path"]).exists()

    # 5. 撤销
    logs = client.get(f"{BASE}/logs", params={"limit": 50}).json()
    archive_log = next(l for l in logs if l["operation_type"] == "ARCHIVE" and l["document_id"] == doc_id)
    r = client.post(f"{BASE}/logs/{archive_log['id']}/undo")
    r.raise_for_status()
    print(f"[5] 已撤销，文件恢复: {r.json()['restored_path']}")
    assert test_file.exists()

    # 6. Dashboard
    stats = client.get(f"{BASE}/system/dashboard/stats").json()
    print(f"[6] Dashboard: 总文档 {stats['total_documents']} | 已归档 {stats['total_archived']}")

    # 7. 清理测试数据
    client.delete(f"{BASE}/documents/{doc_id}")
    if test_file.exists():
        test_file.unlink()
    print("[7] 测试数据已清理")

    print("\n✅ 端到端验证全部通过")


if __name__ == "__main__":
    main()
