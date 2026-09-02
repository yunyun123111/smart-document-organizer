# -*- coding: utf-8 -*-
"""智能文档自动整理系统 - 便携版启动器

双击「启动.bat」即可运行：自动启动后端服务并打开浏览器，
关闭本窗口即停止系统。本文件位于便携包 app/ 根目录。
"""
import os
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
PYTHON = BASE / "python" / "python.exe"
PORT = 8000


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def main() -> None:
    if not PYTHON.exists():
        print("未找到内置 Python 环境（python\\python.exe），请检查便携包是否完整。")
        input("按回车键关闭...")
        return
    os.chdir(BASE)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BASE)
    print("正在启动智能文档自动整理系统 ...")
    proc = subprocess.Popen(
        [
            str(PYTHON), "-m", "uvicorn", "backend.main:app",
            "--host", "0.0.0.0", "--port", str(PORT),
        ],
        cwd=str(BASE),
        env=env,
    )
    try:
        started = False
        for _ in range(60):
            if port_open(PORT):
                started = True
                break
            if proc.poll() is not None:
                print("后端进程异常退出，请查看上方错误信息。")
                input("按回车键关闭...")
                return
            time.sleep(1)
        if not started:
            print("服务启动超时（60 秒）。")
        else:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
            print(f"系统已启动：http://127.0.0.1:{PORT}")
            print("浏览器将自动打开。关闭本窗口即停止系统。")
            try:
                input("\n按回车键停止系统...")
            except EOFError:
                pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("系统已停止。")


if __name__ == "__main__":
    main()
