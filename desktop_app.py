# -*- coding: utf-8 -*-
"""桌面版启动器：把系统包成原生窗口（pywebview + WebView2），免去手动开浏览器。

双击「启动系统.bat」即调用本脚本：
- 检测 8000 端口 → 未运行则启动 uvicorn 子进程
- 打开内嵌应用窗口（原生窗口，非浏览器）
- 关闭窗口即停止本次启动的后端服务

若本机缺少 pywebview 依赖或窗口创建失败，自动回退为「打开系统浏览器」模式。
所有错误均写入 data/desktop_app.log，便于排查。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import traceback
import webbrowser
import json
from pathlib import Path

# 安装形态默认只监听本机（避免 Windows 防火墙弹窗与端口暴露）；
# 源码运行保留局域网访问能力。可用 SDO_HOST 显式覆盖。
IS_FROZEN = bool(getattr(sys, "frozen", False))
HOST = os.environ.get("SDO_HOST", "127.0.0.1" if IS_FROZEN else "0.0.0.0").strip() or "127.0.0.1"
# 端口不再写死：默认由系统分配空闲端口，SDO_PORT 可固定（部署机排障用）
PORT = 0
BASE = Path(__file__).resolve().parent
try:
    from backend.config import DATA_HOME  # 安装形态下指向 %APPDATA%
except Exception:
    DATA_HOME = BASE / "data"
LOG = DATA_HOME / "desktop_app.log"
LOCK = DATA_HOME / ".desktop_app.pid"
WINDOW_TITLE = "智能文档整理系统"
WINDOW_WIDTH = 1380
WINDOW_HEIGHT = 900


def pick_port() -> int:
    """返回可用端口：优先环境变量，其次由系统分配。"""
    raw = os.environ.get("SDO_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(msg)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def wait_port(port: int, timeout: float = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        time.sleep(0.5)
    return False


def read_lock() -> dict:
    """读取实例锁。兼容旧版纯 pid 文本格式。"""
    try:
        txt = LOCK.read_text(encoding="utf-8").strip()
        if not txt:
            return {}
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        return {"pid": int(txt)}
    except Exception:
        return {}


def lock_owner_alive(info: dict) -> bool:
    try:
        pid = int(info.get("pid", 0))
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_lock(port: int) -> bool:
    """防重复实例：已有实例存活时返回 False（并带上它的端口）。"""
    info = read_lock()
    if info and lock_owner_alive(info):
        return False
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(
            json.dumps({"pid": os.getpid(), "port": port, "host": "127.0.0.1"}),
            encoding="utf-8",
        )
    except Exception:
        pass  # 锁不可用时放行，避免误拦
    return True


def release_lock() -> None:
    try:
        info = read_lock()
        if info.get("pid") == os.getpid():
            LOCK.unlink()
    except Exception:
        pass


def main() -> int:
    os.chdir(BASE)
    log("=" * 46)
    log("   智能文档整理系统  桌面版")
    log("=" * 46)

    existing = read_lock()
    if existing and lock_owner_alive(existing):
        p = int(existing.get("port") or 8000)
        log(f"[提示] 系统已在运行，请直接访问 http://127.0.0.1:{p}")
        return 0

    port = pick_port()
    if not acquire_lock(port):
        info = read_lock()
        p = int(info.get("port") or 8000)
        log(f"[提示] 系统已在运行，请直接访问 http://127.0.0.1:{p}")
        return 0

    own_server = False
    proc = None
    try:
        if port_in_use(port):
            log(f"[系统] 端口 {port} 已有服务在运行，直接打开窗口...")
        else:
            log(f"[系统] 正在启动后端服务（端口 {port}），请稍候...")
            try:
                if IS_FROZEN:
                    cmd = [sys.executable, "--serve", "--host", HOST, "--port", str(port)]
                else:
                    cmd = [sys.executable, "-m", "uvicorn", "backend.main:app",
                           "--host", HOST, "--port", str(port)]
                proc = subprocess.Popen(cmd, cwd=str(BASE))
            except Exception as e:
                log(f"[错误] 后端启动失败: {e}")
                return 1
            own_server = True
            if not wait_port(port):
                log(f"[错误] 后端启动超时（40 秒内未监听 {port} 端口），请检查后端依赖与日志。")
                try:
                    proc.terminate()
                except Exception:
                    pass
                return 1

        url = f"http://127.0.0.1:{port}"
        log(f"[系统] 本机访问: {url}")
        log(f"[系统] 局域网访问: http://{local_ip()}:{port}")
        log("[系统] 正在打开桌面窗口，关闭窗口即退出系统...")

        try:
            import webview  # 延迟导入：缺失时回退浏览器模式
        except Exception:
            log("[系统] 未安装 pywebview，改用系统浏览器打开。")
            webbrowser.open(url)
            if own_server:
                try:
                    proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
            return 0

        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass

        try:
            window = webview.create_window(
                WINDOW_TITLE,
                url,
                width=WINDOW_WIDTH,
                height=WINDOW_HEIGHT,
                min_size=(1024, 700),
            )
        except Exception as e:
            log(f"[错误] 创建窗口失败: {e}")
            log("[系统] 改用系统浏览器打开。")
            webbrowser.open(url)
            return 0

        try:
            webview.start()
        except Exception as e:
            log(f"[错误] 窗口启动异常: {e}")
            log("[系统] 改用系统浏览器打开。")
            webbrowser.open(url)

        # 窗口已关闭：停止本次启动的后端
        if own_server:
            log("[系统] 窗口已关闭，正在停止服务...")
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        log("[系统] 已退出。")
        return 0
    except Exception:
        log("[错误] 发生未预期异常：")
        log(traceback.format_exc())
        return 1
    finally:
        release_lock()


def _ensure_streams() -> None:
    """窗口模式（无控制台）下 stdout/stderr 为 None，uvicorn 的日志处理器会炸。

    这里把输出接到数据目录的 server.log，既避免报错，也方便装完后排障。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        f = open(LOG.parent / "server.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
    except Exception:
        pass


def serve_cli() -> int:
    """打包形态的后端入口：`exe --serve --host H --port P`。

    PyInstaller 下 sys.executable 就是 exe 本身，不能再 `-m uvicorn`，
    所以主进程用这个参数把自己拉起成一个纯后端进程。
    """
    _ensure_streams()
    host, port = HOST, 8000
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
        elif a == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass
    import uvicorn
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    if "--serve" in sys.argv:
        sys.exit(serve_cli())
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[系统] 已退出。")
        sys.exit(0)
