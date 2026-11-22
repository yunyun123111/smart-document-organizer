@echo off
rem Silent-friendly: can be run by Task Scheduler every 10 minutes.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync.ps1" %*
exit /b %errorlevel%