@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist "logs" mkdir logs
if not exist "data"  mkdir data

echo ============================================================
echo   Kokoro TTS Stack - Launcher
echo   STT (8100) + TTS (8990) + HTTP client (5500)
echo ============================================================

REM --- 1. Kill any previous processes on the three ports.
echo.
echo [1/4] Killing previous processes on 8100 / 8990 / 5500 ...
set "KILLED=0"
for %%P in (8100 8990 5500) do (
    for /f "tokens=5" %%I in ('netstat -aon ^| findstr /R /C:":%%P$" ^| findstr LISTENING') do (
        taskkill /F /PID %%I >nul 2>&1
        if not errorlevel 1 (
            echo       killed PID %%I on port %%P
            set /a "KILLED+=1"
        )
    )
)
if "%KILLED%"=="0" echo       (no previous processes found)
timeout /t 2 /nobreak >nul

REM --- 2-4. Launch each service detached. Each _run_*.cmd handles
REM        its own env + redirection, so quoting stays simple.
echo.
echo [2/4] Starting STT server  (port 8100, logs\stt_server.log)
start "Kokoro-STT-8100" /B "%ROOT%_run_stt.cmd"
echo   [OK] STT launched in background

echo.
echo [3/4] Starting TTS server  (port 8990, logs\tts_server.log)
start "Kokoro-TTS-8990" /B "%ROOT%_run_tts.cmd"
echo   [OK] TTS launched in background

echo.
echo [4/4] Starting HTTP client  (port 5500, logs\http_client.log)
start "Kokoro-HTTP-5500" /B "%ROOT%_run_http.cmd"
echo   [OK] HTTP launched in background

REM --- 5. Wait + verify.
echo.
echo Waiting 10s for servers to bind...
timeout /t 10 /nobreak >nul

echo.
echo ============================================================
echo   Port status
echo ============================================================
for %%P in (8100 8990 5500) do (
    netstat -aon | findstr /R /C:":%%P$" | findstr LISTENING >nul && (
        echo   port %%P : LISTENING
    ) || (
        echo   port %%P : NOT LISTENING  -- revisar logs\*err.log
    )
)

echo.
echo ============================================================
echo   URLs
echo ============================================================
echo   Cliente (abrir en el browser):
echo     http://localhost:5500/client/client_example.html
echo.
echo   Endpoints:
echo     STT health    http://localhost:8100/health
echo     STT metrics   http://localhost:8100/metrics
echo     STT session   http://localhost:8100/session/state
echo     TTS health    http://localhost:8990/health
echo     TTS metrics   http://localhost:8990/metrics
echo     TTS session   http://localhost:8990/session/state
echo.
echo ============================================================
echo   Logs en vivo (abrir otra terminal):
echo ============================================================
echo   Get-Content .\logs\stt_server.log -Wait
echo   Get-Content .\logs\tts_server.log -Wait
echo.
echo   Para detener todo: ejecutar stop.cmd
echo ============================================================

pause
endlocal
