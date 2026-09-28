"""盖章检测服务测试（Phase 2：seal_detector）。

覆盖：无章纯白（疑似兜底）/ 红块非圆章（未盖章）/ 红圆章 ×1、×2 /
黑白化红章不误判 / 非支持类型跳过 / PDF（首页+末页）路径。
"""
from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageDraw

from backend.config import settings
from backend.database import (
    SessionLocal,
    seed_default_categories,
    seed_default_filename_rules,
    seed_default_rename_templates,
    seed_default_rules,
)
from backend.models import DocumentField, JOB_CANCELLED, JOB_COMPLETED, ProcessingJob
from backend.services.processing_service import processing_service
from backend.services.seal_detector import detect_from_file, detect_from_image


def _blank(w=800, h=600) -> Image.Image:
    return Image.new("RGB", (w, h), "white")


def _add_red_circle(img: Image.Image, cx: int, cy: int, r: int) -> Image.Image:
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(210, 30, 30))
    return img


def _add_red_bar(img: Image.Image) -> Image.Image:
    """红色长条（非圆章，圆形度 < 0.5）。"""
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 300, 130], fill=(200, 40, 40))
    return img


def test_white_image_suspect():
    """纯白无章：无红色 → 疑似（黑白件兜底，不误判 sealed）。"""
    res = detect_from_image(_blank())
    assert res.status == "suspect"
    assert res.detected is False


def test_red_bar_unsealed():
    """有红色但非圆形章 → 未盖章。"""
    res = detect_from_image(_add_red_bar(_blank()))
    assert res.status == "unsealed"
    assert res.count == 0


def test_one_red_seal():
    """红色圆形章 ×1 → sealed，count=1。"""
    img = _add_red_circle(_blank(), cx=400, cy=300, r=50)
    res = detect_from_image(img)
    assert res.status == "sealed"
    assert res.count == 1
    assert res.detected is True
    assert res.confidence > 0


def test_two_red_seals():
    """红色圆形章 ×2 → sealed，count=2。"""
    img = _blank()
    _add_red_circle(img, cx=250, cy=250, r=45)
    _add_red_circle(img, cx=550, cy=350, r=45)
    res = detect_from_image(img)
    assert res.status == "sealed"
    assert res.count == 2


def test_grayscale_seal_suspect():
    """红章灰度化（黑白扫描件）：检测不到 → suspect，不误判 sealed。"""
    img = _add_red_circle(_blank(), cx=400, cy=300, r=50).convert("L").convert("RGB")
    res = detect_from_image(img)
    assert res.status == "suspect"
    assert res.detected is False


def test_skip_non_supported_type(tmp_path):
    """Word/文本等不支持的类型 → skip，不影响主流程。"""
    f = tmp_path / "a.txt"
    f.write_text("销售合同", encoding="utf-8")
    res = detect_from_file(f)
    assert res.skip is True


def test_pdf_seal_detected(tmp_path):
    """PDF（渲染首页+末页）检出红章。"""
    img = _add_red_circle(_blank(), cx=400, cy=300, r=50)
    pdf = tmp_path / "contract.pdf"
    img.save(str(pdf), "PDF", resolution=100.0)
    res = detect_from_file(pdf)
    assert res.status == "sealed"
    assert res.count >= 1


def test_pdf_no_seal_suspect(tmp_path):
    """PDF 纯白页：无红色 → suspect。"""
    pdf = tmp_path / "blank.pdf"
    _blank().save(str(pdf), "PDF", resolution=100.0)
    res = detect_from_file(pdf)
    assert res.status == "suspect"


def _wait_job(job_id: int, statuses: set[str], timeout: float = 30.0) -> ProcessingJob:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job and job.status in statuses:
                db.expunge(job)
                return job
        time.sleep(0.1)
    raise TimeoutError(f"job #{job_id} 未在超时内进入 {statuses}")


def test_seal_detection_in_processing_pipeline(tmp_path, monkeypatch):
    """端到端：红章图片经整理任务处理 → document_fields 落 seal_status=sealed。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "SEAL_DETECT_ENABLED", True)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_default_categories(db)
        seed_default_rules(db)
        seed_default_filename_rules(db)
        seed_default_rename_templates(db)

    img = _add_red_circle(_blank(), cx=400, cy=300, r=50)
    src = Path(settings.inbox_root) / "合同A.png"
    img.save(str(src))

    job = processing_service.start_job()
    _wait_job(job.id, {JOB_COMPLETED, JOB_CANCELLED})
    with SessionLocal() as db:
        rows = (
            db.query(DocumentField)
            .filter(DocumentField.field_name == "seal_status")
            .all()
        )
        assert rows, "处理链路应写入 seal_status 字段"
        assert rows[0].field_value == "sealed"


def test_seal_detection_disabled_skips_pipeline(tmp_path, monkeypatch):
    """关闭盖章检测开关：处理链路不写入 seal 字段。"""
    monkeypatch.setattr(settings, "INBOX_ROOT", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(settings, "SEAL_DETECT_ENABLED", False)
    Path(settings.inbox_root).mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_default_categories(db)
        seed_default_rules(db)
        seed_default_filename_rules(db)
        seed_default_rename_templates(db)

    img = _add_red_circle(_blank(), cx=400, cy=300, r=50)
    src = Path(settings.inbox_root) / "合同B.png"
    img.save(str(src))

    job = processing_service.start_job()
    _wait_job(job.id, {JOB_COMPLETED, JOB_CANCELLED})
    with SessionLocal() as db:
        rows = (
            db.query(DocumentField)
            .filter(DocumentField.field_name == "seal_status")
            .all()
        )
        assert rows == [], "开关关闭时不应写入 seal 字段"
