@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem First-time setup on a new computer: downloads the runtime, installs the virtual audio driver, starts the app.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" -StartApp
echo.
pause
