@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup-from-zero.ps1" -StartApp
echo.
pause
