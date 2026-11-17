"""
Системные окна «Открыть / Сохранить / Выбрать папку» без pywebview.
Нужны, когда лаунчер открыт в окне браузера (Edge): там свои диалоги недоступны.
Windows — стандартные окна Windows через PowerShell; другие системы — tkinter (если есть).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _win_filter(file_types) -> str:
    """('Моды (*.jar)', ...) -> 'Моды (*.jar)|*.jar|...|Все файлы|*.*'"""
    parts = []
    for ft in file_types or ():
        match = re.search(r"\(([^)]*)\)", ft)
        if match:
            parts.append(f"{ft}|{match.group(1)}")
    parts.append("Все файлы (*.*)|*.*")
    return "|".join(parts)


def _powershell(kind: str, file_types=(), save_filename: str = "", allow_multiple: bool = False):
    env = dict(os.environ, NL_FILTER=_win_filter(file_types), NL_NAME=save_filename or "")
    if kind == "folder":
        dialog = "$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.ShowNewFolderButton = $true"
        result = "$d.SelectedPath"
    elif kind == "save":
        dialog = ("$d = New-Object System.Windows.Forms.SaveFileDialog; $d.Filter = $env:NL_FILTER; "
                  "$d.FileName = $env:NL_NAME; $d.InitialDirectory = [Environment]::GetFolderPath('Desktop')")
        result = "$d.FileName"
    else:
        dialog = ("$d = New-Object System.Windows.Forms.OpenFileDialog; $d.Filter = $env:NL_FILTER; "
                  f"$d.Multiselect = ${'true' if allow_multiple else 'false'}")
        result = "$d.FileNames -join [char]10"
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
        # невидимое окно-владелец поверх всех окон: диалог появляется над лаунчером, даже если тот на весь экран
        "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $true; Text = 'VaultMC — выбор файла'; "
        "Opacity = 0; FormBorderStyle = 'None'; StartPosition = 'CenterScreen'; Width = 1; Height = 1}; "
        "$owner.Show(); $owner.Activate(); "
        f"{dialog}; $r = $d.ShowDialog($owner); $owner.Close(); if ($r -eq 'OK') {{ {result} }}"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script],
                         capture_output=True, env=env, timeout=600, creationflags=0x08000000)
    text = out.stdout.decode("utf-8", "replace").strip()
    if not text:
        return None
    if kind == "open" and allow_multiple:
        return [line.strip() for line in text.splitlines() if line.strip()]
    return text.splitlines()[0].strip()


def _tk(kind: str, file_types=(), save_filename: str = "", allow_multiple: bool = False):
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    types = []
    for ft in file_types or ():
        match = re.search(r"\(([^)]*)\)", ft)
        if match:
            types.append((ft, match.group(1).replace(";", " ")))
    types.append(("Все файлы", "*"))
    try:
        if kind == "folder":
            result = filedialog.askdirectory()
        elif kind == "save":
            result = filedialog.asksaveasfilename(initialfile=save_filename, filetypes=types)
        elif allow_multiple:
            result = list(filedialog.askopenfilenames(filetypes=types))
        else:
            result = filedialog.askopenfilename(filetypes=types)
    finally:
        root.destroy()
    return result or None


def native_dialog(kind: str, **kw):
    if os.name == "nt":
        return _powershell(kind, kw.get("file_types", ()), kw.get("save_filename", ""), kw.get("allow_multiple", False))
    try:
        return _tk(kind, kw.get("file_types", ()), kw.get("save_filename", ""), kw.get("allow_multiple", False))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Окно выбора файлов недоступно: {exc}") from None


def downloads_dir() -> Path:
    path = Path.home() / "Downloads"
    return path if path.is_dir() else Path.home()
