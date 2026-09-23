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
from pathlib import Path

HOST = "0.0.0.0"
PORT = 8000
BASE = Path(__file__).resolve().parent
LOG = BASE / "data" / "desktop_app.log"
LOCK = BASE / "data" / ".desktop_app.pid"
WINDOW_TITLE = "智能文档整理系统"
WINDOW_WIDTH = 1380
WINDOW_HEIGHT = 900


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


def wait_port(timeout: float = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(PORT):
            return True
        time.sleep(0.5)
    return False


def acquire_lock() -> bool:
    """防重复实例：已有 desktop_app 在跑时返回 False（不重复弹窗）。"""
    try:
        if LOCK.exists():
            try:
                pid = int(LOCK.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)  # 进程存在则不抛异常
                return False
            except (ValueError, OSError):
                pass  # 锁文件过期（进程已退出）
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return True  # 锁不可用时放行，避免误拦


def release_lock() -> None:
    try:
        if LOCK.exists() and LOCK.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK.unlink()
    except Exception:
        pass


def main() -> int:
    os.chdir(BASE)
    log("=" * 46)
    log("   智能文档整理系统  桌面版")
    log("=" * 46)

    if not acquire_lock():
        log("[提示] 系统已在运行（桌面版或后端服务），请直接查看已打开的窗口。")
        log("[提示] 若未见窗口，请在浏览器打开 http://127.0.0.1:8000")
        return 0

    own_server = False
    proc = None
    try:
        if port_in_use(PORT):
            log(f"[系统] 服务已在运行（端口 {PORT}），直接打开窗口...")
        else:
            log("[系统] 正在启动后端服务，请稍候...")
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "backend.main:app",
                     "--host", HOST, "--port", str(PORT)],
                    cwd=str(BASE),
                )
            except Exception as e:
                log(f"[错误] 后端启动失败: {e}")
                return 1
            own_server = True
            if not wait_port():
                log("[错误] 后端启动超时（40 秒内未监听 8000 端口），请检查后端依赖与日志。")
                try:
                    proc.terminate()
                except Exception:
                    pass
                return 1

        url = f"http://127.0.0.1:{PORT}"
        log(f"[系统] 本机访问: {url}")
        log(f"[系统] 局域网访问: http://{local_ip()}:{PORT}")
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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[系统] 已退出。")
        sys.exit(0)
