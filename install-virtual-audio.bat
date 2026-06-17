@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup-virtual-audio.ps1" -OpenDownloadPage
echo.
pause
