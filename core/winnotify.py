"""Уведомления Windows для друзей: окошко «Ник хочет в друзья» / «Ник открыл мир», когда лаунчер свёрнут.

Как работает: окошко показывает PowerShell (WinRT-API Windows 10/11, ничего не ставится) от имени приложения «VaultMC»
(запись в HKCU\\Software\\Classes\\AppUserModelId, права администратора не нужны). Текст в скрипт НЕ вшивается: он идёт через
переменные окружения и экранируется в самом скрипте — ник друга, даже с кавычками и $(...), не может выполнить команду.
Клик по окошку открывает vaultmc://friends — лаунчер поднимается на вкладку «Друзья».
Запасной вариант, если Windows не показывает окошки («Не беспокоить», выключено в параметрах): мигание значка на панели задач.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys

APP_ID = "VaultMC"
CREATE_NO_WINDOW = 0x08000000

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$esc = { param($s) [System.Security.SecurityElement]::Escape([string]$s) }
$xml = "<toast activationType='protocol' launch='$(& $esc $env:VMC_LAUNCH)'><visual><binding template='ToastGeneric'>" +
       "<text>$(& $esc $env:VMC_TITLE)</text><text>$(& $esc $env:VMC_TEXT)</text></binding></visual></toast>"
if ($env:VMC_DRY -eq '1') { Write-Output $xml; exit 0 }
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:VMC_APPID)
if ($null -eq $notifier.Setting) {
    # Windows не знает приложение «VaultMC» (нет ярлыка с AppUserModelID): показываем от имени Windows PowerShell —
    # заголовок окошка будет «Windows PowerShell», зато оно точно покажется. Нажатие по нему всё равно открывает лаунчер.
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe')
}
if ($notifier.Setting -ne 'Enabled') { Write-Output ('disabled:' + $notifier.Setting); exit 0 }
$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($doc))
Write-Output 'shown'
"""


def encoded_script() -> str:
    return base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")


def register_app(icon: str = "") -> None:
    """Имя «VaultMC» и значок для окошек (Windows показывает их над текстом)."""
    if sys.platform != "win32":
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "VaultMC")
        winreg.SetValueEx(k, "ShowInSettings", 0, winreg.REG_DWORD, 1)
        if icon and os.path.isfile(icon):
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon)


def _run(title: str, text: str, launch: str, dry: bool = False, timeout: float = 20.0) -> tuple[int, str, str]:
    env = {**os.environ, "VMC_TITLE": title[:120], "VMC_TEXT": text[:240], "VMC_LAUNCH": launch, "VMC_APPID": APP_ID,
           "VMC_DRY": "1" if dry else "0"}
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script()],
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def toast(title: str, text: str, launch: str = "vaultmc://friends", icon: str = "") -> str:
    """'shown' — окошко показано; 'disabled:<причина>' — Windows их не показывает; 'error:<текст>' — не вышло."""
    if sys.platform != "win32":
        return "error:только Windows"
    try:
        register_app(icon)
        code, out, err = _run(title, text, launch)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error:{exc}"
    if code != 0:
        return "error:" + (err or out)[:200]
    return out.splitlines()[-1] if out else "error:пустой ответ"


def preview_xml(title: str, text: str, launch: str = "vaultmc://friends") -> str:
    """XML окошка так, как его соберёт скрипт (для проверки экранирования): ничего не показывает."""
    code, out, err = _run(title, text, launch, dry=True)
    if code != 0:
        raise RuntimeError(err or out)
    return out


def flash_window(title: str = "VaultMC", count: int = 3) -> bool:
    """Мигание значка на панели задач (если окно не на виду). Работает без разрешений на окошки."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x2 | 0xC, count, 0)   # FLASHW_TRAY | FLASHW_TIMERNOFG
    user32.FlashWindowEx(ctypes.byref(info))
    return True
