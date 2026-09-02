@echo off
title 智能文档整理系统 - 一键启动
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3 并勾选 "Add to PATH"。
    pause
    exit /b 1
)
python launcher.py
