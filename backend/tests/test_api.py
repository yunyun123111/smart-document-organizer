"""API 集成测试：用 TestClient 走通完整业务流（上传→批量整理→审核→归档→撤销）。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app


CONTRACT_TXT = (
    "销售合同\n"
    "合同编号：XS202608001\n"
    "甲方：ABC有限公司\n"
    "乙方：DEF有限公司\n"
    "合同金额：人民币 128,500.00 元\n"
    "签订日期：2026年08月20日\n"
    "本合同由甲乙双方根据《中华人民共和国民法典》订立。\n"
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试独立临时库 + 独立 inbox + 不持久化配置。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    # 禁止 settings.update() 把测试配置写回真实 .env
    monkeypatch.setattr(settings, "_persist", lambda *a, **k: None)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


def _wait_job(client: TestClient, job_id: int, timeout: float = 60.0):
    """轮询任务直至结束。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/processing/{job_id}")
        assert r.status_code == 200
        job = r.json()
        if job["status"] in ("completed", "cancelled", "failed"):
            return job
        time.sleep(0.2)
    raise TimeoutError("job 未在超时内完成")


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/system/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestCategories:
    def test_tree_has_defaults(self, client):
        r = client.get("/api/categories")
        assert r.status_code == 200
        tree = r.json()
        assert tree  # 有默认分类
        names = {c["name"] for c in tree}
        assert "合同" in names

    def test_crud(self, client):
        r = client.post("/api/categories", json={"name": "测试分类"})
        assert r.status_code == 200
        cid = r.json()["id"]
        assert r.json()["path"] == "测试分类"

        r = client.put(f"/api/categories/{cid}", json={"description": "d"})
        assert r.status_code == 200

        r = client.delete(f"/api/categories/{cid}")
        assert r.status_code == 200


class TestUploadAndProcessing:
    def _upload(self, client, name: str, content: str, suffix: str):
        r = client.post(
            "/api/documents/upload",
            files={"file": (name, content.encode("utf-8"), "text/plain")},
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_upload_and_full_flow(self, client):
        # 1. 上传合同 → pending
        up = self._upload(client, "销售合同.txt", CONTRACT_TXT, ".txt")
        assert up["status"] == "pending"

        # 2. 开始批量整理
        r = client.post("/api/processing/start", json={})
        assert r.status_code == 200, r.text
        job = _wait_job(client, r.json()["id"])
        assert job["total_files"] == 1
        assert job["processed_files"] == 1
        assert job["review_count"] == 1  # 合同置信度~0.8 → 待审核
        assert job["status"] == "completed"

        # 3. 待审核列表有 1 项
        r = client.get("/api/review")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        doc_id = items[0]["id"]

        # 4. 审核详情
        r = client.get(f"/api/review/{doc_id}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["document"]["document_type"] == "销售合同"
        assert "suggested_filename" in detail
        assert detail["suggested_category"] == "合同/销售合同"

        # 5. 确认归档
        r = client.post(f"/api/review/{doc_id}/approve", json={})
        assert r.status_code == 200, r.text

        # 6. 文档状态变为 archived
        r = client.get(f"/api/documents/{doc_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "archived"
        archived_path = Path(r.json()["current_path"])
        assert archived_path.exists()
        assert "销售合同" in str(archived_path)

        # 7. 日志 + 撤销
        r = client.get("/api/logs")
        assert r.status_code == 200
        logs = r.json()
        assert logs
        archive_log = next(l for l in logs if l["operation_type"] == "ARCHIVE")
        r = client.post(f"/api/logs/{archive_log['id']}/undo")
        assert r.status_code == 200, r.text
        assert Path(settings.inbox_root).joinpath("销售合同.txt").exists()

    def test_duplicate_processing(self, client):
        """同一文件两次处理 → 第二次识别为重复。"""
        self._upload(client, "a.txt", CONTRACT_TXT, ".txt")
        r = client.post("/api/processing/start", json={})
        job = _wait_job(client, r.json()["id"])
        assert job["review_count"] == 1

        # 再次上传同一文件并处理
        r = client.post(
            "/api/documents/upload",
            files={"file": ("a.txt", CONTRACT_TXT.encode("utf-8"), "text/plain")},
        )
        # 上传时即检测到重复
        assert r.status_code == 200
        assert r.json()["status"] == "duplicate"

    def test_reject_unknown(self, client):
        self._upload(client, "随笔.txt", "今天天气不错，出门散步。", ".txt")
        r = client.post("/api/processing/start", json={})
        job = _wait_job(client, r.json()["id"])
        assert job["review_count"] == 1  # 无法判断 → 待审核


class TestReviewActions:
    def test_skip(self, client):
        client.post(
            "/api/documents/upload",
            files={"file": ("x.txt", CONTRACT_TXT.encode("utf-8"), "text/plain")},
        )
        r = client.post("/api/processing/start", json={})
        _wait_job(client, r.json()["id"])

        r = client.get("/api/review")
        doc_id = r.json()[0]["id"]
        r = client.post(f"/api/review/{doc_id}/skip")
        assert r.status_code == 200
        r = client.get(f"/api/documents/{doc_id}")
        assert r.json()["status"] == "skipped"

    def test_update_category_does_not_duplicate_field(self, client):
        """改建议分类应更新唯一字段，而不是每次都插一条新记录。"""
        client.post(
            "/api/documents/upload",
            files={"file": ("y.txt", "今天天气不错，出门散步。", "text/plain")},
        )
        r = client.post("/api/processing/start", json={})
        _wait_job(client, r.json()["id"])
        doc_id = client.get("/api/review").json()[0]["id"]

        for _ in range(3):
            rr = client.put(
                f"/api/review/{doc_id}",
                json={"fields": {"suggested_category": "项目资料", "company": "甲公司"}},
            )
            assert rr.status_code == 200, rr.text

        fields = client.get(f"/api/documents/{doc_id}").json()["fields"]
        cat_fields = [f for f in fields if f["field_name"] == "suggested_category"]
        assert len(cat_fields) == 1, f"suggested_category 被重复插入: {cat_fields}"
        assert cat_fields[0]["field_value"] == "项目资料"
        assert [f for f in fields if f["field_name"] == "company"][0]["field_value"] == "甲公司"

    def test_approve_persists_chosen_category(self, client):
        """确认归档时用户手选的分类要落库，供后续查看/撤销使用。"""
        client.post(
            "/api/documents/upload",
            files={"file": ("z.txt", "今天天气不错，出门散步。", "text/plain")},
        )
        r = client.post("/api/processing/start", json={})
        _wait_job(client, r.json()["id"])
        doc_id = client.get("/api/review").json()[0]["id"]

        rr = client.post(
            f"/api/review/{doc_id}/approve",
            json={"category_path": "证照"},
        )
        assert rr.status_code == 200, rr.text
        fields = client.get(f"/api/documents/{doc_id}").json()["fields"]
        cat = [f for f in fields if f["field_name"] == "suggested_category"]
        assert cat and cat[0]["field_value"] == "证照"


class TestSettings:
    def test_get_put(self, client, monkeypatch):
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert "document_root" in r.json()

        original = settings.REVIEW_THRESHOLD
        try:
            r = client.put("/api/settings", json={"review_threshold": 0.55})
            assert r.status_code == 200
            assert r.json()["review_threshold"] == 0.55
        finally:
            settings.REVIEW_THRESHOLD = original  # 恢复全局配置，避免污染其他测试

    def test_dashboard_stats(self, client):
        r = client.get("/api/system/dashboard/stats")
        assert r.status_code == 200
        body = r.json()
        assert "total_documents" in body
        assert "pending_review" in body


class TestBatchReview:
    """批量确认 / 聚类分组 / 模板学习 回归。"""

    @staticmethod
    def _contract(no: str, amount: str = "128,500.00") -> str:
        """生成内容不同但可控的合同文本（避免被重复检测拦截）。"""
        return (
            CONTRACT_TXT.replace("XS202608001", no)
            .replace("128,500.00", amount)
        )

    def _make_review_docs(self, client, specs: list[tuple[str, str]]) -> list[int]:
        """specs: [(文件名, 合同号)] 或 [(文件名, 合同号, 金额)]"""
        for spec in specs:
            name, no = spec[0], spec[1]
            amount = spec[2] if len(spec) > 2 else "128,500.00"
            content = self._contract(no, amount)
            client.post(
                "/api/documents/upload",
                files={"file": (name, content.encode("utf-8"), "text/plain")},
            )
        r = client.post("/api/processing/start", json={})
        _wait_job(client, r.json()["id"])
        items = client.get("/api/review").json()
        return [it["id"] for it in items]

    def test_batch_approve_all(self, client):
        ids = self._make_review_docs(client, [("合同A.txt", "XS001"), ("合同B.txt", "XS002")])
        assert len(ids) == 2
        r = client.post("/api/review/batch-approve", json={"doc_ids": ids})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success_count"] == 2
        assert body["failed_count"] == 0
        for it in ids:
            d = client.get(f"/api/documents/{it}").json()
            assert d["status"] == "archived"

    def test_batch_approve_partial_failure(self, client):
        ids = self._make_review_docs(client, [("合同C.txt", "XS003"), ("合同D.txt", "XS004")])
        # 先单条归档第一个 → 它不再处于待审核
        r = client.post(f"/api/review/{ids[0]}/approve", json={})
        assert r.status_code == 200, r.text
        r = client.post("/api/review/batch-approve", json={"doc_ids": ids})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success_count"] == 1
        assert body["failed_count"] == 1

    def test_learn_template_on_approve(self, client):
        """确认归档后自动学习识别模板（同类下次直接归档）。"""
        ids = self._make_review_docs(client, [("合同E.txt", "XS005")])
        r = client.post(f"/api/review/{ids[0]}/approve", json={})
        assert r.status_code == 200, r.text
        tpls = client.get("/api/review/templates").json()
        assert any(
            t["document_type"] == "销售合同" and t["category_path"] == "合同/销售合同"
            for t in tpls
        )

    def test_review_groups_by_contract_no(self, client):
        """相同合同号的文件应聚类为同一组（智能批处理）。"""
        client.post(
            "/api/documents/upload",
            files={"file": ("A.txt", self._contract("XS006", "128,500.00").encode("utf-8"), "text/plain")},
        )
        client.post(
            "/api/documents/upload",
            files={"file": ("B.txt", self._contract("XS006", "200,000.00").encode("utf-8"), "text/plain")},
        )
        r = client.post("/api/processing/start", json={})
        _wait_job(client, r.json()["id"])
        groups = client.get("/api/review/groups").json()
        contract_groups = [g for g in groups if g["group_key"].startswith("contract:")]
        assert any(len(g["documents"]) >= 2 for g in contract_groups), f"未按合同号聚类: {groups}"
