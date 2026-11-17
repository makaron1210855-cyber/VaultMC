@echo off
rem Ярлык VaultMC на рабочем столе БЕЗ сборки .exe (антивирус не мешает).
rem Ярлык запускает лаунчер через установленный Python, без окна консоли.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (echo Python не найден. Установите с python.org с галочкой "Add Python to PATH". & pause & exit /b 1)
echo Устанавливаю зависимости...
%PY% -m pip install -r requirements.txt
echo.
echo Создаю ярлык...
%PY% -c "import sys; sys.path.insert(0, '.'); from core.utils import create_desktop_shortcut as c; p = c(); print('Ярлык создан:', p) if p else sys.exit(1)" || (echo Не удалось создать ярлык. & pause & exit /b 1)
echo.
echo Готово! На рабочем столе появился ярлык VaultMC.
echo Не перемещайте и не удаляйте эту папку - ярлык запускает лаунчер из неё.
pause
