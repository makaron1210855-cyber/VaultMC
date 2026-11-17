@echo off
rem ============================================================================
rem  VaultMC - установка в один клик.
rem  Сам ставит Python (если нужно), копирует лаунчер, делает ярлык и запускает.
rem ============================================================================
chcp 65001 >nul
title Установка VaultMC
cd /d "%~dp0"
color 0A
echo.
echo   ██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗███╗   ███╗ ██████╗
echo   ██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝████╗ ████║██╔════╝
echo   ██║   ██║███████║██║   ██║██║     ██║   ██╔████╔██║██║
echo   ╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║   ██║╚██╔╝██║██║
echo    ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║   ██║ ╚═╝ ██║╚██████╗
echo     ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝   ╚═╝     ╚═╝ ╚═════╝
echo.

rem ---- 0. Запущено прямо из архива? ----
if not exist "%~dp0main.py" (
  echo   [!] Сначала распакуйте архив:
  echo       правой кнопкой по архиву - "Извлечь все" - "Извлечь".
  echo       Потом откройте папку и снова нажмите INSTALL.bat
  echo.
  pause
  exit /b 1
)

set "TARGET=%LOCALAPPDATA%\VaultMC"

rem ---- 1. Python ----
echo   [1/4] Проверяю Python...
set "PY="
call :try_python py
if not defined PY call :try_python python
if not defined PY call :try_python "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo         Python не найден - устанавливаю ^(1-3 минуты^)...
  winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
  if not exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    echo         Скачиваю Python с python.org...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe' -OutFile \"$env:TEMP\python-setup.exe\""
    "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
  )
  call :try_python "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)
if not defined PY (
  echo.
  echo   [!] Не получилось установить Python автоматически.
  echo       Скачайте его с https://www.python.org/downloads/
  echo       При установке поставьте галочку "Add Python to PATH",
  echo       потом снова нажмите INSTALL.bat
  pause
  exit /b 1
)
echo         OK

rem ---- 2. Копирование ----
echo   [2/4] Копирую лаунчер в %TARGET% ...
robocopy "%~dp0." "%TARGET%" /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ dist build >nul
if errorlevel 8 (
  echo   [!] Не удалось скопировать файлы. Закройте лаунчер, если он открыт, и попробуйте снова.
  pause
  exit /b 1
)
echo         OK

rem ---- 3. Компоненты ----
echo   [3/4] Устанавливаю компоненты окна...
"%PY%" -m pip install --disable-pip-version-check -q -r "%TARGET%\requirements.txt" >nul 2>nul
echo         OK ^(если компонент не встанет - лаунчер откроется в окне Edge, это нормально^)

rem ---- 4. Ярлык и запуск ----
echo   [4/4] Создаю ярлык на рабочем столе...
"%PY%" -c "import sys; sys.path.insert(0, r'%TARGET%'); from core.utils import create_desktop_shortcut as c; sys.exit(0 if c() else 1)"
if errorlevel 1 (echo         Ярлык создать не удалось - запускайте файл start.bat в папке %TARGET%) else (echo         OK)

echo.
echo   ======================================================
echo     ГОТОВО! Ярлык VaultMC - на рабочем столе.
echo     Лаунчер сейчас откроется сам.
echo   ======================================================
echo.
rem запускаем через pythonw.exe - без чёрного окна консоли
"%PY%" -c "import sys, pathlib; p = pathlib.Path(sys.executable).with_name('pythonw.exe'); print(p if p.exists() else sys.executable)" > "%TEMP%\neon_pyw.txt"
set /p PYW=<"%TEMP%\neon_pyw.txt"
pushd "%TARGET%"
start "" "%PYW%" "%TARGET%\main.py"
popd
timeout /t 6 >nul
exit /b 0

rem ---- помощник: подходит ли этот Python (3.10+) ----
:try_python
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0
