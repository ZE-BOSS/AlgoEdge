@echo off
setlocal EnableDelayedExpansion

:: ============================================================
:: AlgoEdge Trading Bot Launcher
:: ============================================================

title AlgoEdge Trading Bot Launcher
color 0A

echo.
echo ============================================================
echo                AlgoEdge Trading Bot
echo ============================================================
echo.

:: ── Check .env ──────────────────────────────────────────────

echo [1/5] Checking configuration...

if not exist ".env" (
    echo ERROR: .env file not found.
    pause
    exit /b 1
)

echo OK

:: ── Check Virtual Environment ───────────────────────────────

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found.
    echo Run:
    echo py -3.12 -m venv .venv
    pause
    exit /b 1
)

if not exist "logs" mkdir logs

:: ── Start MT5 ───────────────────────────────────────────────

echo.
echo [2/5] Starting MetaTrader 5...

tasklist /FI "IMAGENAME eq terminal64.exe" | find /I "terminal64.exe" >nul

if errorlevel 1 (
    if exist "C:\Program Files\MetaTrader 5\terminal64.exe" (
        start "" "C:\Program Files\MetaTrader 5\terminal64.exe"
        timeout /t 10 >nul
        echo MT5 launched.
    ) else (
        echo WARNING: MT5 not found.
    )
) else (
    echo MT5 already running.
)

:: ── Start Backend ───────────────────────────────────────────

echo.
echo [3/5] Starting FastAPI backend...

start "AlgoEdge Backend" cmd /k ^
".venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000"

echo Waiting for backend...

set READY=0

for /L %%i in (1,1,30) do (
    curl -s http://localhost:8000/api/health >nul 2>&1

    if !errorlevel! EQU 0 (
        set READY=1
        goto backend_ready
    )

    timeout /t 1 >nul
)

:backend_ready

if "!READY!"=="1" (
    echo Backend healthy.
) else (
    echo WARNING: Backend not responding yet.
    echo Check backend console window.
)

:: ── Start Frontend ──────────────────────────────────────────

echo.
echo [4/5] Starting React/Vite frontend...

start "AlgoEdge Frontend" cmd /k ^
"cd frontend && npm run dev"

timeout /t 5 >nul

:: ── Open Browser ────────────────────────────────────────────

echo.
echo [5/5] Opening dashboard...

start http://localhost:5173

echo.
echo ============================================================
echo Dashboard : http://localhost:5173
echo API Docs  : http://localhost:8000/docs
echo Health    : http://localhost:8000/api/health
echo ============================================================
echo.
echo Backend and frontend logs are visible in their own windows.
echo Close those windows to stop the services.
echo.

pause