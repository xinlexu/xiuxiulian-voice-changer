@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Debug start: same as the exe but keeps a console window with live logs.
if not exist "%~dp0runtime\python.exe" (
  echo runtime\python.exe not found. Run the first-time setup first.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" -X utf8 "%~dp0app\main.py" --console %*
echo.
pause
