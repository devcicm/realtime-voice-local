@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ============================================================
echo   Kokoro TTS Stack - Stopper
echo ============================================================
echo.

for %%P in (8100 8990 5500) do (
    echo   Killing listeners on port %%P ...
    for /f "tokens=5" %%I in ('netstat -aon ^| findstr /R /C:":%%P$" ^| findstr LISTENING') do (
        taskkill /F /PID %%I >nul 2>&1
        if not errorlevel 1 echo       killed PID %%I
    )
)

echo.
echo   Killing any leftover uvicorn / http.server python processes ...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Kokoro-STT-8100*"  >nul 2>&1
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Kokoro-TTS-8990*"  >nul 2>&1
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Kokoro-HTTP-5500*" >nul 2>&1

echo.
echo Done.
pause
endlocal
