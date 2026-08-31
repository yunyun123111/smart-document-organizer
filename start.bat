@echo off
chcp 65001 >nul
title Smart Document Organizer - 启动器
cd /d %~dp0

echo ============================================
echo   Smart Document Organizer 启动器
echo ============================================
echo.

echo [0/2] 检查环境...
where python >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未找到 python 命令，请先安装 Python 3.12+
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未找到 npm 命令，请先安装 Node.js
    pause
    exit /b 1
)
echo   python / npm 已就绪

echo.
echo [1/2] 启动后端 (FastAPI 127.0.0.1:8000)...
start "SDO-Backend" cmd /k "cd /d %~dp0 && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul

echo [2/2] 启动前端 (Vite 127.0.0.1:5173)...
start "SDO-Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
timeout /t 3 /nobreak >nul

echo.
echo ============================================
echo   启动完成！
echo   请在浏览器打开:  http://127.0.0.1:5173
echo   后端接口文档:    http://127.0.0.1:8000/docs
echo ============================================
echo.
echo 提示：关闭对应的命令行窗口即可停止服务。
pause
