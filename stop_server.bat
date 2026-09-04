@echo off
rem Smart Document Organizer - Stop Server
cd /d "%~dp0"
chcp 65001 >nul

echo [INFO] Stopping Smart Document Organizer...

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo [INFO] Killed process %%p (port 8000)
    set FOUND=1
)

if %FOUND%==0 (
    echo [INFO] No server process found on port 8000. It is already stopped.
) else (
    echo [INFO] Server stopped.
)

pause
