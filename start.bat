@echo off
:: ============================================================
:: AlgoEdge Trading Bot — Windows Startup Script
:: Supports: Standard MT5 (Forex/Gold) + Deriv MT5 (Synthetics)
:: ============================================================

title AlgoEdge Trading Bot Launcher
color 0A

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║     AlgoEdge Trading Bot v1.0            ║
echo  ║     Starting all services...             ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ── Check .env exists ───────────────────────────────────────
echo [1/6] Checking configuration...
if not exist ".env" (
    echo       ERROR: .env file not found!
    echo       Copy .env.example to .env and fill in your credentials.
    pause
    exit /b 1
)
echo       Configuration OK.

:: ── Start Standard MT5 (Forex/Gold) ─────────────────────────
echo [2/6] Checking MetaTrader 5 (Standard)...
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I /N "terminal64.exe" >NUL
if "%ERRORLEVEL%"=="1" (
    echo       Launching Standard MT5...
    start "" "C:\Program Files\MetaTrader 5\terminal64.exe"
    timeout /t 12 /nobreak >NUL
    echo       Standard MT5 started.
) else (
    echo       Standard MT5 already running. OK.
)

:: ── Start Deriv MT5 (Synthetics) ────────────────────────────
echo [3/6] Checking Deriv MT5 (Synthetics - optional)...
:: Look for Deriv MT5 in common install locations
set DERIV_MT5_PATH=""
if exist "C:\Program Files\Deriv MT5\terminal64.exe" set DERIV_MT5_PATH="C:\Program Files\Deriv MT5\terminal64.exe"
if exist "%APPDATA%\MetaQuotes\Terminal\Deriv\terminal64.exe" set DERIV_MT5_PATH="%APPDATA%\MetaQuotes\Terminal\Deriv\terminal64.exe"

if %DERIV_MT5_PATH%=="" (
    echo       Deriv MT5 not found — skipping (not required if not trading synthetics)
) else (
    echo       Launching Deriv MT5 for synthetic indices...
    start "" %DERIV_MT5_PATH%
    timeout /t 10 /nobreak >NUL
    echo       Deriv MT5 started.
)

:: ── Start Redis ─────────────────────────────────────────────
echo [4/6] Starting Redis server...
tasklist /FI "IMAGENAME eq redis-server.exe" 2>NUL | find /I /N "redis-server.exe" >NUL
if "%ERRORLEVEL%"=="1" (
    start "Redis" /MIN redis-server.exe
    timeout /t 2 /nobreak >NUL
    echo       Redis started on port 6379.
) else (
    echo       Redis already running. OK.
)

:: ── Start Python Backend ────────────────────────────────────
echo [5/6] Starting Python backend (FastAPI)...
if not exist "logs" mkdir logs
cd backend
start "AlgoEdge Backend" /MIN cmd /c "uvicorn main:app --host 0.0.0.0 --port 8000 --reload > ..\logs\backend.log 2>&1"
cd ..
timeout /t 5 /nobreak >NUL

:: Health check
curl -s http://localhost:8000/api/health >NUL 2>&1
if "%ERRORLEVEL%"=="0" (
    echo       Backend healthy on http://localhost:8000
) else (
    echo       Backend starting... check logs\backend.log if issues persist
)

:: ── Start Frontend ──────────────────────────────────────────
echo [6/6] Starting Frontend (React/Vite PWA)...
cd frontend
start "AlgoEdge Frontend" /MIN cmd /c "npm run dev > ..\logs\frontend.log 2>&1"
cd ..
timeout /t 4 /nobreak >NUL

:: ── Summary ─────────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║  AlgoEdge is running!                                ║
echo  ║                                                       ║
echo  ║  Dashboard:   http://localhost:5173                   ║
echo  ║  API Docs:    http://localhost:8000/docs              ║
echo  ║  API Health:  http://localhost:8000/api/health        ║
echo  ║                                                       ║
echo  ║  📱 Install as app: Open dashboard → browser menu     ║
echo  ║     → "Add to Home Screen" or "Install App"          ║
echo  ║                                                       ║
echo  ║  Instruments: Volatility 75 (Deriv) + XAUUSD (Gold)  ║
echo  ║  Compounding: See Settings → Risk → Compounding       ║
echo  ║                                                       ║
echo  ║  Logs: .\logs\backend.log + .\logs\frontend.log       ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

start http://localhost:5173
echo  Press any key to close launcher (services continue running in background)
pause >NUL
