# -*- coding: utf-8 -*-
"""创建桌面快捷方式（P1-3）。

用 PowerShell -EncodedCommand（UTF-16LE Base64）调用 WScript.Shell 创建 .lnk，
完全规避命令行传参的中文编码问题。
"""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
BAT = BASE / "启动系统.bat"

PS_SCRIPT = """$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = $ws.CreateShortcut($desktop + '\\智能文档整理.lnk')
$lnk.TargetPath = '{bat}'
$lnk.WorkingDirectory = '{workdir}'
$lnk.IconLocation = 'shell32.dll,43'
$lnk.Description = '智能文档整理系统 一键启动'
$lnk.Save()
Write-Output 'OK'
""".format(
    bat=str(BAT).replace("\\", "\\\\"),
    workdir=str(BASE).replace("\\", "\\\\"),
)


def main() -> int:
    if not BAT.exists():
        print(f"[错误] 未找到 {BAT.name}，请确认脚本位于项目根目录。")
        return 1
    try:
        encoded = base64.b64encode(PS_SCRIPT.encode("utf-16-le")).decode("ascii")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            print("[错误] 创建快捷方式失败:")
            print(proc.stderr.decode("gbk", "ignore") or proc.stdout.decode("gbk", "ignore"))
            return 1
        print("已在桌面创建快捷方式「智能文档整理」，双击即可启动系统。")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[错误] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
