@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup-from-zero.ps1" -OpenDriverDownload -StartApp
echo.
pause
