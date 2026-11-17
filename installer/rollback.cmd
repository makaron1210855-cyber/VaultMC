@echo off
rem VaultMC: return the previous launcher version.
rem The installer keeps the previous version next to the current one on every update (app.prev, python.prev).
rem This file is copied to the install folder and started by the "return previous version" shortcut
rem or by the button in VaultMC settings. Player data (%USERPROFILE%\.vaultmc) is never touched.
rem Keep this file ASCII-only: cmd.exe breaks on UTF-8 text.
setlocal
cd /d "%~dp0"
if not exist "app.prev\main.py" (
  echo There is no previous VaultMC version to return to.
  timeout /t 6 >nul
  exit /b 1
)
echo Closing VaultMC...
taskkill /IM VaultMC.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
if exist "app.bad" rmdir /s /q "app.bad"
if exist "python.bad" rmdir /s /q "python.bad"
ren "app" "app.bad" || goto fail
ren "app.prev" "app" || goto undo
if exist "python.prev\VaultMC.exe" (
  ren "python" "python.bad" && ren "python.prev" "python"
)
rmdir /s /q "app.bad"
if exist "python.bad" rmdir /s /q "python.bad"
echo Previous version restored. Starting VaultMC...
start "" "%~dp0python\VaultMC.exe" "%~dp0app\main.py"
exit /b 0

:undo
ren "app.bad" "app"
:fail
echo Could not swap the folders - some files are in use. Close VaultMC and the game, then try again.
timeout /t 10 >nul
exit /b 1
