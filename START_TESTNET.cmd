@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title Woozoo Binance Spot Testnet
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-phase8-testnet.ps1"
set "woozoo_exit_code=%ERRORLEVEL%"

if "%woozoo_exit_code%"=="0" exit /b 0

echo.
echo [Woozoo] Startup failed. Review the error above, then run again.
pause

exit /b %woozoo_exit_code%
