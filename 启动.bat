@echo off
chcp 65001 >nul
title Smart Document Organizer - Launcher
cd /d "%~dp0"

echo ============================================
echo    Smart Document Organizer  -  One-Click Start
echo ============================================
echo.

rem --- check frontend dist exists ---
if not exist "frontend\dist\index.html" (
    echo [WARN] Frontend build not found. Building now...
    pushd frontend
    call npm run build
    popd
    if errorlevel 1 (
        echo [ERROR] Frontend build failed. Please check node/npm.
        pause
        exit /b 1
    )
)

rem --- check if already running ---
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo System is already running. Opening browser...
    start "" "http://127.0.0.1:8000"
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

echo Starting backend service...
echo (Keep this window open. Close it to stop the system.)
echo.
start "SmartDoc-Backend" cmd /k "cd /d "%~dp0" && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"

echo Waiting for service ready...
ping -n 7 127.0.0.1 >nul

echo Opening browser...
start "" "http://127.0.0.1:8000"

echo.
echo ============================================
echo    System started:  http://127.0.0.1:8000
echo    Close the backend window to stop.
echo ============================================
ping -n 4 127.0.0.1 >nul
