"""Запуск собранного сервера на этом ПК: процесс, журнал, команды в консоль, остановка.

Остановка всегда через команду stop (сервер сохраняет мир), и только если он не закрылся за 45 с — принудительно.
Процесс привязан к «заданию» Windows (Job Object) с флагом «закрыть вместе с лаунчером»: если лаунчер упадёт или его
закроют из диспетчера задач, сервер не останется висеть в фоне без окна.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from .utils import is_windows, no_window_flags

DONE_RE = re.compile(r"\bDone \([\d.,]+s\)!")
PORT_RE = re.compile(r"Starting Minecraft server on [^:\s]*:(\d{2,5})")
CRASH_MARK = "This crash report has been saved to"
STOP_WAIT = 45
_jobs: list = []        # ручки заданий Windows живут, пока жив лаунчер


def console_java(path: str) -> str:
    """javaw.exe → java.exe рядом (у сервера есть консоль; javaw иногда не пишет в канал до первого заполнения буфера)."""
    p = Path(path)
    if p.name.lower() == "javaw.exe" and (p.parent / "java.exe").is_file():
        return str(p.parent / "java.exe")
    return path


def _kill_with_parent(proc: subprocess.Popen) -> None:
    if not is_windows():
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXT()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(wintypes.HANDLE(job))
            return
        handle = k32.OpenProcess(0x0001 | 0x0100, False, proc.pid)    # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if handle:
            if k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
                _jobs.append(job)
            else:
                k32.CloseHandle(wintypes.HANDLE(job))
            k32.CloseHandle(wintypes.HANDLE(handle))
    except (OSError, AttributeError):
        pass


class LocalServer:
    """Один сервер за раз. post(event, payload): server_log str, server_state dict."""

    def __init__(self, post: Callable[[str, object], None]):
        self.post = post
        self.proc: subprocess.Popen | None = None
        self.instance = ""
        self.state = "off"           # off | starting | running | stopping
        self.port = 0
        self.lines: deque[str] = deque(maxlen=400)
        self._lock = threading.Lock()
        self.exit_code: int | None = None

    def status(self) -> dict:
        return {"state": self.state, "instance": self.instance, "port": self.port, "lines": list(self.lines),
                "exit_code": self.exit_code}

    def _set(self, state: str) -> None:
        self.state = state
        self.post("server_state", {"state": state, "instance": self.instance, "port": self.port,
                                   "exit_code": self.exit_code})

    def start(self, folder: Path, instance_id: str, java: str, ram_gb: int, launch: list[str], port: int) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                raise RuntimeError("Сервер уже запущен")
            cmd = [console_java(java), f"-Xmx{ram_gb}G", f"-Xms{min(ram_gb, 1)}G", "-Dfile.encoding=UTF-8", *launch]
            self.lines.clear()
            self.instance, self.port, self.exit_code = instance_id, port, None
            self.proc = subprocess.Popen(cmd, cwd=str(folder), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, creationflags=no_window_flags())
            _kill_with_parent(self.proc)
            self._set("starting")
            self._line(f"[VaultMC] Запуск: {' '.join(cmd[:4])} ...")
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _line(self, text: str) -> None:
        self.lines.append(text)
        self.post("server_log", text)

    def _read(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            self._line(line)
            m = PORT_RE.search(line)
            if m:
                self.port = int(m.group(1))
            if self.state == "starting" and DONE_RE.search(line):
                self._set("running")
            if CRASH_MARK in line:
                self._crashed(proc)
        code = proc.wait()
        if proc is self.proc:
            self.exit_code = code
            self._line(f"[VaultMC] Сервер остановлен (код {code})")
            self.proc = None
            self._set("off")

    def _crashed(self, proc: subprocess.Popen) -> None:
        """Сервер упал (отчёт о вылете записан), но Java часто остаётся висеть — закрываем её сами через 15 с."""
        self._line("[VaultMC] Сервер упал — причина выше (Caused by) и в папке crash-reports. Частая причина — "
                   "несовместимые моды: снимите галочку с мода из ошибки и нажмите «Обновить сервер»")

        def later():
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
        threading.Thread(target=later, daemon=True).start()

    def command(self, text: str) -> None:
        text = (text or "").strip().lstrip("/")
        proc = self.proc
        if not text or not proc or proc.poll() is not None:
            return
        if "\n" in text or "\r" in text or len(text) > 1000:
            raise ValueError("Одна команда — одна строка")
        self._line(f"> {text}")
        try:
            proc.stdin.write((text + "\n").encode("utf-8"))
            proc.stdin.flush()
        except OSError:
            pass

    def stop(self, wait: bool = False) -> None:
        proc = self.proc
        if not proc or proc.poll() is not None:
            return
        self._set("stopping")
        try:
            proc.stdin.write(b"stop\n")
            proc.stdin.flush()
        except OSError:
            pass

        def watchdog():
            try:
                proc.wait(STOP_WAIT)
            except subprocess.TimeoutExpired:
                self._line("[VaultMC] Сервер не закрылся сам за 45 с — закрываю принудительно")
                proc.kill()
        if wait:
            watchdog()
        else:
            threading.Thread(target=watchdog, daemon=True).start()

    def running_for(self, instance_id: str) -> bool:
        return self.instance == instance_id and self.proc is not None and self.proc.poll() is None


def local_addresses() -> list[str]:
    """Адреса ПК в локальной сети (и Radmin/Hamachi): по ним заходят друзья рядом."""
    import socket
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in found:   # 169.254 — адрес без сети, по нему не зайти
                found.append(ip)
    except OSError:
        pass
    return found[:4]
