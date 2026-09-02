@echo off
chcp 65001 >nul
cd /d %~dp0
title 智能文档自动整理系统
"python\python.exe" launch.py
pause
