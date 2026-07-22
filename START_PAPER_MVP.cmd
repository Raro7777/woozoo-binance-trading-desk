@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title Woozoo Paper MVP
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-paper-mvp.ps1"
set "woozoo_exit_code=%ERRORLEVEL%"

if not "%woozoo_exit_code%"=="0" (
  echo.
  echo [Woozoo] Startup failed. Check the error shown above, then try again.
  pause
)

exit /b %woozoo_exit_code%
