@echo off
title SmartDoc - Publish Update
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" %*
if errorlevel 1 (
    echo.
    echo [ERROR] Publish failed. See the messages above.
    pause
    exit /b 1
)
echo.
echo Done. Press any key to close this window.
pause >nul