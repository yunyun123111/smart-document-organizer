@echo off
title Smart Document Organizer - First Setup
cd /d "%~dp0"

echo ============================================
echo  Smart Document Organizer - first-time setup
echo ============================================
echo.

echo [1/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.12 64-bit
    echo         from python.org and tick "Add python.exe to PATH".
    echo         After installing, close this window and run it again.
    pause
    exit /b 1
)
python --version
echo.

echo [2/3] Installing dependencies (3-10 min, needs internet)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed. Try a mirror:
    echo   python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)
echo.

echo [3/3] Creating config file...
if exist .env (
    echo .env already exists, keeping it.
) else (
    copy .env.example .env >nul
    echo Created .env from .env.example.
)
echo.

echo Done. Next step: double-click the start .bat in this folder.
echo If documents show as missing, use Settings - Relocate documents.
echo.
pause