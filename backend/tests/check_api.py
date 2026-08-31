"""验证前端核心 API 响应状态（排查"全部操作失败"）。"""
import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000/api", timeout=30)

checks = [
    ("GET", "/documents"),
    ("GET", "/review"),
    ("GET", "/categories"),
    ("GET", "/rules"),
    ("GET", "/logs"),
    ("GET", "/settings"),
    ("GET", "/system/dashboard/stats"),
    ("GET", "/processing/3"),
]
for method, path in checks:
    try:
        r = c.request(method, path)
        print(f"{r.status_code} {method} {path}")
    except Exception as e:
        print(f"ERR  {method} {path}: {e}")

review = c.get("/review").json()
if review:
    rid = review[0]["id"]
    r = c.get(f"/review/{rid}")
    print(f"{r.status_code} GET /review/{rid} (detail)")
    d = r.json()
    print("  suggested_category:", d.get("suggested_category"), "| filename:", d.get("suggested_filename"))
    r2 = c.post(
        f"/review/{rid}/approve",
        json={
            "document_type": d["document"]["document_type"],
            "category_path": d["suggested_category"],
        },
    )
    print(f"{r2.status_code} POST /review/{rid}/approve -> {r2.text[:150]}")
else:
    print("无待审核项")
