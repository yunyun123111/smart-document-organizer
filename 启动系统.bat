@echo off
title 智能文档整理系统 - 桌面版
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3 并勾选 "Add to PATH"。
    pause
    exit /b 1
)
python desktop_app.py
if errorlevel 1 (
    echo [错误] 桌面版启动失败，请查看 data\desktop_app.log 了解原因。
    echo [系统] 尝试传统浏览器模式...
    python launcher.py
)
pause
