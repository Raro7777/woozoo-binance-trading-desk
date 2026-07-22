@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title Woozoo Paper MVP 종료
corepack pnpm paper:mvp:stop
set "woozoo_exit_code=%ERRORLEVEL%"

echo.
if "%woozoo_exit_code%"=="0" (
  echo [Woozoo] PostgreSQL과 Redis 컨테이너를 종료했습니다. 데이터는 보존됩니다.
) else (
  echo [Woozoo] 종료에 실패했습니다. 위 오류를 확인하세요.
)
pause

exit /b %woozoo_exit_code%
