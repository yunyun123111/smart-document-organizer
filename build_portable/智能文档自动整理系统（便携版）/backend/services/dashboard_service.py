"""Dashboard 趋势统计与异常提醒（独立可测）。

统计口径：
- 每日新增：按 documents.created_at 日期分组
- 识别成功率：当日新增中 (processed + archived + need_review) / 当日新增
  （已产出识别结果即算成功；failed 为识别失败）
- OCR 失败率：按 operation_logs(operation_type=OCR) 的 result 统计
异常提醒：
- 某分类暴增：近 7 天某类型新增 >= 5 且 >= 前 7 天的 3 倍（前 7 天为 0 时新增 >= 5 即提醒）
- OCR 失败率上升：今日失败率 >= 30%，或近 7 天失败率 >= 20%（且失败样本 >= 3）
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models import (
    STATUS_ARCHIVED,
    STATUS_DUPLICATE,
    STATUS_FAILED,
    STATUS_NEED_REVIEW,
    STATUS_PROCESSED,
    Document,
    OperationLog,
)

# 识别结果类状态
_SUCCESS_STATUSES = {STATUS_PROCESSED, STATUS_ARCHIVED, STATUS_NEED_REVIEW}
_ALL_STATUS_KEYS = [
    STATUS_PROCESSED,
    STATUS_ARCHIVED,
    STATUS_NEED_REVIEW,
    STATUS_FAILED,
    STATUS_DUPLICATE,
]

# 暴增/OCR 阈值
TYPE_SURGE_MIN_COUNT = 5        # 近 7 天至少多少份
TYPE_SURGE_MULTIPLIER = 3       # 相对前 7 天的倍数
OCR_TODAY_FAIL_RATE = 0.30      # 今日失败率阈值
OCR_WEEK_FAIL_RATE = 0.20       # 近 7 天失败率阈值
OCR_MIN_FAIL_SAMPLES = 3        # 至少失败几次才提醒


def _date_range(days: int) -> list[date]:
    today = date.today()
    return [today - timedelta(days=i) for i in range(days - 1, -1, -1)]


def build_daily(db: Session, days: int = 14) -> list[dict]:
    """近 N 天每日新增 / 归档 / 待确认 / 失败 与识别成功率。"""
    dates = _date_range(days)
    keys = {d.isoformat(): d for d in dates}
    daily = {
        iso: {"date": iso, "new": 0, "archived": 0, "need_review": 0,
              "processed": 0, "failed": 0, "duplicate": 0}
        for iso in keys
    }

    rows = (
        db.query(
            func.date(Document.created_at).label("d"),
            Document.status,
            func.count(Document.id),
        )
        .group_by("d", Document.status)
        .all()
    )
    for d, status, cnt in rows:
        if d not in daily:
            continue
        daily[d]["new"] += cnt
        if status in daily[d]:
            daily[d][status] += cnt

    result = []
    for iso in keys:
        item = daily[iso]
        total = item["new"]
        ok = item["processed"] + item["archived"] + item["need_review"]
        item["success_rate"] = round(ok / total, 4) if total else None
        item["fail_count"] = item["failed"]
        result.append(item)
    return result


def build_ocr_daily(db: Session, days: int = 14) -> list[dict]:
    """近 N 天 OCR 操作数与失败率。"""
    dates = _date_range(days)
    daily = {d.isoformat(): {"date": d.isoformat(), "total": 0, "failed": 0}
             for d in dates}

    rows = (
        db.query(
            func.date(OperationLog.created_at).label("d"),
            OperationLog.result,
            func.count(OperationLog.id),
        )
        .filter(OperationLog.operation_type == "OCR")
        .group_by("d", OperationLog.result)
        .all()
    )
    for d, result, cnt in rows:
        if d not in daily:
            continue
        daily[d]["total"] += cnt
        if result == "failed":
            daily[d]["failed"] += cnt

    out = []
    for iso in dates:
        item = daily[iso.isoformat()]
        total = item["total"]
        item["fail_rate"] = round(item["failed"] / total, 4) if total else None
        out.append(item)
    return out


def detect_alerts(db: Session, days: int = 14) -> list[dict]:
    """异常提醒：分类暴增 / OCR 失败率上升。"""
    alerts: list[dict] = []
    today = date.today()
    last7_start = today - timedelta(days=6)
    prev7_start = today - timedelta(days=13)

    # ---- 分类暴增 ----
    type_rows = (
        db.query(
            func.date(Document.created_at).label("d"),
            Document.document_type,
            func.count(Document.id),
        )
        .filter(Document.created_at >= datetime.combine(prev7_start, datetime.min.time()))
        .group_by("d", Document.document_type)
        .all()
    )
    last7: dict[str, int] = defaultdict(int)
    prev7: dict[str, int] = defaultdict(int)
    for d, t, cnt in type_rows:
        t = (t or "").strip() or "未识别"
        if last7_start <= date.fromisoformat(d) <= today:
            last7[t] += cnt
        elif prev7_start <= date.fromisoformat(d) < last7_start:
            prev7[t] += cnt

    for t, cnt in last7.items():
        prev = prev7.get(t, 0)
        surge = cnt >= TYPE_SURGE_MIN_COUNT and (
            (prev > 0 and cnt >= prev * TYPE_SURGE_MULTIPLIER) or (prev == 0)
        )
        if surge:
            direction = f"（近7天 {cnt} 份 vs 前7天 {prev} 份）"
            alerts.append({
                "level": "warning",
                "kind": "type_surge",
                "type": t,
                "message": f"「{t}」近 7 天新增 {cnt} 份，疑似集中涌入{direction}",
                "link": f"/library?type={t}",
            })

    # ---- OCR 失败率 ----
    ocr_daily = build_ocr_daily(db, days=7)
    today_item = ocr_daily[-1]
    if today_item["total"] > 0:
        rate = today_item["fail_rate"]
        if rate is not None and rate >= OCR_TODAY_FAIL_RATE and today_item["failed"] >= 1:
            alerts.append({
                "level": "error",
                "kind": "ocr_fail",
                "type": None,
                "message": f"今日 OCR 失败率 {rate * 100:.0f}%"
                           f"（失败 {today_item['failed']}/{today_item['total']} 次），"
                           f"请检查扫描件清晰度或 OCR 服务",
                "link": "/logs",
            })

    week_total = sum(i["total"] for i in ocr_daily)
    week_failed = sum(i["failed"] for i in ocr_daily)
    if week_total >= 5 and week_failed >= OCR_MIN_FAIL_SAMPLES:
        week_rate = week_failed / week_total
        if week_rate >= OCR_WEEK_FAIL_RATE:
            alerts.append({
                "level": "warning",
                "kind": "ocr_fail",
                "type": None,
                "message": f"近 7 天 OCR 失败率 {week_rate * 100:.0f}%"
                           f"（失败 {week_failed}/{week_total} 次）呈上升趋势",
                "link": "/logs",
            })

    return alerts


def get_dashboard_trends(db: Session, days: int = 14) -> dict:
    """Dashboard 趋势 + 异常提醒 完整数据。"""
    return {
        "days": days,
        "daily": build_daily(db, days),
        "ocr_daily": build_ocr_daily(db, days),
        "alerts": detect_alerts(db, days),
    }
