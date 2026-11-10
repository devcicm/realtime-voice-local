@echo off
setlocal
set "PYTHONUNBUFFERED=1"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
if not defined BIND_HOST set "BIND_HOST=127.0.0.1"
python -m uvicorn server.stt.stt_server:app --host %BIND_HOST% --port 8100 --log-level info > logs\stt_server.log 2> logs\stt_server.err.log
endlocal
