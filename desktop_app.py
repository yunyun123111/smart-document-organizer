# -*- coding: utf-8 -*-
"""桌面版启动器：把系统包成原生窗口（pywebview + WebView2），免去手动开浏览器。

双击「启动系统.bat」即调用本脚本：
- 检测 8000 端口 → 未运行则启动 uvicorn 子进程
- 打开内嵌应用窗口（原生窗口，非浏览器）
- 关闭窗口即停止本次启动的后端服务

若本机缺少 pywebview 依赖，自动回退为「打开系统浏览器」的传统模式。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HOST = "0.0.0.0"
PORT = 8000
BASE = Path(__file__).resolve().parent
WINDOW_TITLE = "智能文档整理系统"
WINDOW_WIDTH = 1380
WINDOW_HEIGHT = 900


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


def main() -> None:
    os.chdir(BASE)
    print("=" * 46)
    print("   智能文档整理系统  桌面版")
    print("=" * 46)

    own_server = False
    if port_in_use(PORT):
        print(f"[系统] 服务已在运行（端口 {PORT}），直接打开窗口...")
    else:
        print("[系统] 正在启动后端服务，请稍候...")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.main:app",
                 "--host", HOST, "--port", str(PORT)],
                cwd=str(BASE),
            )
        except Exception as e:
            print(f"[错误] 后端启动失败: {e}")
            input("按回车键退出...")
            return
        own_server = True
        if not wait_port():
            print("[错误] 后端启动超时，请检查上方日志。")
            try:
                proc.terminate()
            except Exception:
                pass
            input("按回车键退出...")
            return

    url = f"http://127.0.0.1:{PORT}"
    print(f"[系统] 本机访问: {url}")
    print(f"[系统] 局域网访问: http://{local_ip()}:{PORT}")
    print("[系统] 正在打开桌面窗口，关闭窗口即退出系统...")

    try:
        import webview  # 延迟导入：缺失时回退浏览器模式
    except Exception:
        print("[系统] 未安装 pywebview，改用系统浏览器打开。")
        webbrowser.open(url)
        if own_server:
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
        return

    # 允许窗口内下载文件（EdgeChromium 模式）
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(1024, 700),
    )
    try:
        webview.start()
    except Exception as e:
        print(f"[错误] 窗口启动失败: {e}")
        print("[系统] 改用系统浏览器打开。")
        webbrowser.open(url)

    # 窗口已关闭：停止本次启动的后端
    if own_server:
        print("[系统] 窗口已关闭，正在停止服务...")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    print("[系统] 已退出。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[系统] 已退出。")
