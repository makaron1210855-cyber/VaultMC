; =============================================================================
;  Установщик VaultMC (NSIS). Собирается командой:  makensis installer/vaultmc.nsi
;  Внутри: лаунчер + готовый Python 3.12 с компонентами окна. Ничего ставить отдельно не нужно.
;  Ставится без прав администратора в %LOCALAPPDATA%\Programs\VaultMC.
;  Данные игрока (~\.vaultmc: сборки, миры, аккаунты) при удалении НЕ стираются.
; =============================================================================
Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APPNAME "VaultMC"
!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VaultMC"
!define OLD_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\NeonLauncher"
!ifndef BUNDLE
  !define BUNDLE "..\build\bundle"
!endif

Name "${APPNAME}"
OutFile "..\dist\VaultMC-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\VaultMC"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APPNAME} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1049 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1049 "FileDescription" "Установщик ${APPNAME}"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "LegalCopyright" "VaultMC"

!define MUI_ICON "..\assets\logo.ico"
!define MUI_UNICON "..\assets\logo.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Установка VaultMC"
!define MUI_WELCOMEPAGE_TEXT "Лаунчер Minecraft: сборки, моды, Ely.by, игра с друзьями.$\r$\n$\r$\nЛицензия не нужна. Всё необходимое уже внутри — просто нажмите «Установить».$\r$\n$\r$\nЕсли лаунчер открыт, закройте его перед установкой."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!define MUI_FINISHPAGE_RUN_TEXT "Запустить VaultMC"

!define MUI_COMPONENTSPAGE_NODESC
!define MUI_PAGE_HEADER_TEXT "Что установить"
!define MUI_PAGE_HEADER_SUBTEXT "Отметьте нужные ярлыки и нажмите «Установить»."
!define MUI_COMPONENTSPAGE_TEXT_TOP "Галочки можно не трогать — всё нужное уже отмечено."

!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

!macro KillRunning
  ; Закрываем запущенный VaultMC (и старый Neon), иначе python3.dll занят и файлы не перезаписать
  System::Call 'Kernel32::SetEnvironmentVariable(t "VMC_DIR", t "$INSTDIR")i'
  System::Call 'Kernel32::SetEnvironmentVariable(t "VMC_OLD", t "$LOCALAPPDATA\Programs\NeonLauncher")i'
  nsExec::ExecToLog `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ($$_.ExecutablePath -and ($$_.ExecutablePath.StartsWith($$env:VMC_DIR + '\', 'OrdinalIgnoreCase') -or $$_.ExecutablePath.StartsWith($$env:VMC_OLD + '\', 'OrdinalIgnoreCase'))) } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"`
  Pop $0
  Sleep 1000
!macroend

Function LaunchApp
  SetOutPath "$INSTDIR\app"
  Exec '"$INSTDIR\python\VaultMC.exe" "$INSTDIR\app\main.py"'
FunctionEnd

Var WantDesktop
Var IsUpdate

Function SkipIfUpdate
  StrCmp $IsUpdate "1" 0 +2
    Abort
FunctionEnd

Section "VaultMC (обязательно)" SecMain
  SectionIn RO
  !insertmacro KillRunning
  ; прежнее название лаунчера (Neon Launcher): убираем программу и ярлыки, данные игрока не трогаем
  RMDir /r "$LOCALAPPDATA\Programs\NeonLauncher"
  Delete "$DESKTOP\Neon Launcher.lnk"
  RMDir /r "$SMPROGRAMS\Neon Launcher"
  DeleteRegKey HKCU "${OLD_UNINST_KEY}"
  ; старые файлы программы удаляем (данные игрока лежат в другой папке и не трогаются)
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File /r "${BUNDLE}\*.*"
  ; VaultMC.exe (копия Python с логотипом) собран в tools/release.py — он уже внутри ${BUNDLE}

  ; «Приложения» в параметрах Windows
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "VaultMC"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\app\assets\logo.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  ; .mrpack: VaultMC появляется в «Открыть с помощью» и открывает сборку двойным щелчком.
  ; Ассоциацию по умолчанию не ставим — её выбирает сам игрок (как у любой программы в Windows)
  WriteRegStr HKCU "Software\Classes\VaultMC.mrpack" "" "Сборка Modrinth (VaultMC)"
  WriteRegStr HKCU "Software\Classes\VaultMC.mrpack\DefaultIcon" "" "$INSTDIR\app\assets\logo_win.ico"
  WriteRegStr HKCU "Software\Classes\VaultMC.mrpack\shell\open\command" "" '"$INSTDIR\python\VaultMC.exe" "$INSTDIR\app\main.py" "%1"'
  WriteRegStr HKCU "Software\Classes\.mrpack\OpenWithProgids" "VaultMC.mrpack" ""
  WriteRegStr HKCU "Software\Classes\Applications\VaultMC.exe" "FriendlyAppName" "VaultMC"
  WriteRegStr HKCU "Software\Classes\Applications\VaultMC.exe\DefaultIcon" "" "$INSTDIR\app\assets\logo_win.ico"
  ; без App Paths Windows 11 не находит VaultMC.exe по имени и рисует в «Открыть с помощью» пустой значок
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\VaultMC.exe" "" "$INSTDIR\python\VaultMC.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\VaultMC.exe" "Path" "$INSTDIR\python"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
  WriteRegStr HKCU "Software\Classes\Applications\VaultMC.exe\shell\open\command" "" '"$INSTDIR\python\VaultMC.exe" "$INSTDIR\app\main.py" "%1"'
  StrCpy $WantDesktop "0"
SectionEnd

Section "Ярлык на рабочем столе" SecDesktop
  SetOutPath "$INSTDIR\app"
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\python\VaultMC.exe" '"$INSTDIR\app\main.py"' "$INSTDIR\app\assets\logo.ico" 0
  StrCpy $WantDesktop "1"
SectionEnd

Section "Ярлык в меню «Пуск»" SecStart
  SetOutPath "$INSTDIR\app"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\python\VaultMC.exe" '"$INSTDIR\app\main.py"' "$INSTDIR\app\assets\logo.ico" 0
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Удалить ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section "-Метка установки"
  ; лаунчер прочитает: если ярлык на рабочем столе просили, а Windows его не создала — создаст сам
  FileOpen $0 "$INSTDIR\installed-by-setup.txt" w
  FileWrite $0 "version=${VERSION}$\r$\ndesktop=$WantDesktop$\r$\nupdate=$IsUpdate$\r$\n"
  FileClose $0
SectionEnd

Section "-Перезапуск после автообновления"
  ; обновление из лаунчера (/UPDATE) или тихая установка (/S): сразу открываем новую версию
  StrCmp $IsUpdate "1" launch
  IfSilent launch done
  launch:
    SetAutoClose true
    Call LaunchApp
  done:
SectionEnd

Function .onInit
  StrCpy $IsUpdate "0"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/UPDATE" $R1
  IfErrors +2 0
    StrCpy $IsUpdate "1"
  ; обновление: ярлыки не навязываем — пересоздаём только те, что уже были
  StrCmp $IsUpdate "1" upd
  IfSilent upd done
  upd:
    IfFileExists "$DESKTOP\${APPNAME}.lnk" +2 0
      SectionSetFlags ${SecDesktop} 0
    IfFileExists "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" +2 0
      SectionSetFlags ${SecStart} 0
  done:
FunctionEnd

Section "Uninstall"
  !insertmacro KillRunning
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\installed-by-setup.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\Classes\VaultMC.mrpack"
  DeleteRegKey HKCU "Software\Classes\Applications\VaultMC.exe"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\VaultMC.exe"
  DeleteRegValue HKCU "Software\Classes\.mrpack\OpenWithProgids" "VaultMC.mrpack"
SectionEnd
