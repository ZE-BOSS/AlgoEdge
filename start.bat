@echo off
setlocal EnableDelayedExpansion

:: ============================================================
:: AlgoEdge Trading Bot Launcher
:: ============================================================

title AlgoEdge Trading Bot
color 0A

echo.
echo ============================================================
echo                AlgoEdge Trading Bot
echo ============================================================
echo.

:: ── Configuration ────────────────────────────────────────────
set BACKEND_PORT=8000
set FRONTEND_PORT=5173
set HEALTH_ENDPOINT=http://localhost:%BACKEND_PORT%/api/health
set HEALTH_TIMEOUT=20
set MT5_EXE=C:\Program Files\MetaTrader 5\terminal64.exe
set MT5_WAIT=8

:: Detect venv directory (support both naming conventions)
set VENV_DIR=
if exist "venv\Scripts\python.exe" set VENV_DIR=venv
if exist ".venv\Scripts\python.exe" set VENV_DIR=.venv

:: ── Pre-flight Checks ────────────────────────────────────────

echo [1/5] Pre-flight checks...

if not exist ".env" (
    echo [FAIL] .env file not found. Copy .env.example to .env and configure it.
    pause
    exit /b 1
)

if "%VENV_DIR%"=="" (
    echo [FAIL] Python virtual environment not found.
    echo        Run: py -3.12 -m venv venv
    echo        Then: venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [WARN] Frontend dependencies not installed. Running npm install...
    pushd frontend && npm install && popd
    if errorlevel 1 (
        echo [FAIL] npm install failed.
        pause
        exit /b 1
    )
)

if not exist "logs" mkdir logs
echo [OK] All checks passed.

:: ── Start MT5 ───────────────────────────────────────────────

echo.
echo [2/5] MetaTrader 5...

tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | find /I "terminal64.exe" >nul

if errorlevel 1 (
    if exist "%MT5_EXE%" (
        start "" "%MT5_EXE%"
        echo      Launching MT5, waiting %MT5_WAIT%s for initialization...
        timeout /t %MT5_WAIT% /nobreak >nul
        echo [OK] MT5 launched.
    ) else (
        echo [SKIP] MT5 not found at "%MT5_EXE%".
    )
) else (
    echo [OK] MT5 already running.
)

:: ── Start Backend ───────────────────────────────────────────

echo.
echo [3/5] FastAPI backend on port %BACKEND_PORT%...

:: Check if backend is already running
curl -s %HEALTH_ENDPOINT% >nul 2>&1
if !errorlevel! EQU 0 (
    echo [OK] Backend already running.
    goto skip_backend
)

start "AlgoEdge Backend" /min cmd /k ^
"%VENV_DIR%\Scripts\python.exe -m uvicorn backend.main:app --reload --host 0.0.0.0 --port %BACKEND_PORT% 2>&1 | tee logs\backend.log"

echo      Waiting for health check (max %HEALTH_TIMEOUT%s)...
set READY=0
for /L %%i in (1,1,%HEALTH_TIMEOUT%) do (
    if !READY! EQU 0 (
        curl -s %HEALTH_ENDPOINT% >nul 2>&1
        if !errorlevel! EQU 0 (
            set READY=1
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)

if "!READY!"=="1" (
    echo [OK] Backend healthy.
) else (
    echo [WARN] Backend not responding after %HEALTH_TIMEOUT%s. Check the backend window.
)

:skip_backend

:: ── Start Frontend ──────────────────────────────────────────

echo.
echo [4/5] React/Vite frontend on port %FRONTEND_PORT%...

:: Check if frontend is already running
curl -s http://localhost:%FRONTEND_PORT% >nul 2>&1
if !errorlevel! EQU 0 (
    echo [OK] Frontend already running.
    goto skip_frontend
)

start "AlgoEdge Frontend" /min cmd /k "cd frontend && npm run dev"

timeout /t 3 /nobreak >nul
echo [OK] Frontend started.

:skip_frontend

:: ── Open Browser ────────────────────────────────────────────

echo.
echo [5/5] Opening dashboard...
start http://localhost:%FRONTEND_PORT%

echo.
echo ============================================================
echo  Dashboard : http://localhost:%FRONTEND_PORT%
echo  API Docs  : http://localhost:%BACKEND_PORT%/docs
echo  Health    : %HEALTH_ENDPOINT%
echo ============================================================
echo.
echo  Press any key to exit this launcher.
echo  Backend and frontend keep running in their own windows.
echo.
pause