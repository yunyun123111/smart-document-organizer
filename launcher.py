# -*- coding: utf-8 -*-
"""桌面一键启动器（P1-3）。

双击「启动系统.bat」调用本脚本：
- 检测 8000 端口是否已在运行 → 在运行则直接打开浏览器
- 未运行则启动 uvicorn 子进程、打开浏览器，显示局域网访问地址
- 保持本窗口即服务运行中；关闭窗口即停止服务
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


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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


def main() -> None:
    print("=" * 46)
    print("   智能文档整理系统  一键启动")
    print("=" * 46)
    os.chdir(BASE)

    if port_in_use(PORT):
        print(f"[系统] 服务已在运行（端口 {PORT}），直接打开浏览器...")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return

    print("[系统] 正在启动后端服务，请稍候...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", HOST, "--port", str(PORT)],
        cwd=str(BASE),
    )
    deadline = time.time() + 40
    ok = False
    while time.time() < deadline:
        if port_in_use(PORT):
            ok = True
            break
        if proc.poll() is not None:
            print("[错误] 后端进程异常退出，请检查上方日志后重试。")
            input("按回车键退出...")
            return
        time.sleep(0.5)

    if not ok:
        print("[错误] 启动超时（40 秒），请检查端口占用或依赖是否完整。")
        input("按回车键退出...")
        return

    ip = local_ip()
    print("")
    print("启动成功！")
    print(f"  本机访问:  http://127.0.0.1:{PORT}")
    print(f"  手机访问:  http://{ip}:{PORT}  （需与电脑同一WiFi）")
    print("  访问密码:  如已设置，见系统设置页")
    print("")
    print("  [提示] 本窗口保持开启即服务运行中；")
    print("         关闭本窗口将同时停止服务。")
    print("=" * 46)
    webbrowser.open(f"http://127.0.0.1:{PORT}")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("收到停止信号，正在停止服务...")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[错误] {e}")
        input("按回车键退出...")
