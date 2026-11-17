@echo off
rem Обычный запуск без консоли. Если не открывается - запустите debug.bat
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -c "import webview" 2>nul || %PY% -m pip install -r requirements.txt
start "" %PY%w main.py
