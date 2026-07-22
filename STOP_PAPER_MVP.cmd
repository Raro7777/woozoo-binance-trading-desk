@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title Stop Woozoo Paper MVP
corepack pnpm paper:mvp:stop
set "woozoo_exit_code=%ERRORLEVEL%"

echo.
if "%woozoo_exit_code%"=="0" (
  echo [Woozoo] PostgreSQL and Redis stopped. Local data was preserved.
) else (
  echo [Woozoo] Shutdown failed. Check the error shown above.
)
pause

exit /b %woozoo_exit_code%
