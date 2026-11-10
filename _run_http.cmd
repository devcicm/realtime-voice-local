@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
python serve_nocache.py 5500 > logs\http_client.log 2> logs\http_client.err.log
endlocal
