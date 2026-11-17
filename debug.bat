@echo off
rem Запуск с консолью: если лаунчер не открывается, здесь будет видно ошибку.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
echo Версия Python:
%PY% --version || (echo Python не найден! Установите с python.org и отметьте "Add Python to PATH". & pause & exit /b 1)
echo.
echo Устанавливаю зависимости...
%PY% -m pip install -r requirements.txt
echo.
echo Запускаю лаунчер...
%PY% main.py
echo.
echo Лаунчер завершился. Если была ошибка - она выше, а также в файле %USERPROFILE%\.vaultmc\crash.log
pause
