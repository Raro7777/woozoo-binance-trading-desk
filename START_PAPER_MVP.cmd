@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title Woozoo Paper MVP
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-paper-mvp.ps1"
set "woozoo_exit_code=%ERRORLEVEL%"

if not "%woozoo_exit_code%"=="0" (
  echo.
  echo [Woozoo] 시작에 실패했습니다. 위 오류를 확인한 뒤 다시 실행하세요.
  pause
)

exit /b %woozoo_exit_code%
