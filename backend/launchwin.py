"""
Окно «Подождите…» на время запуска (как в PineconeMC): этапы, прогресс, лог и «Отмена».

Открывается при нажатии «Играть» и закрывается, когда появилось окно игры или запуск остановлен.
Работает только в окне pywebview. В режиме разработки (браузер) окна нет — хватает карточки в лаунчере.
Python присылает страничке снимок состояния раз в 0,15 с, только когда что-то изменилось.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


def _game_window_state(pid) -> str:
    """Окно процесса игры: «none» — ещё нет, «hung» — Windows пишет «не отвечает», «ok» — отвечает, «gone» — процесса нет."""
    if not pid:
        return "none"
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        h = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "gone"
        code = wintypes.DWORD()
        alive = kernel32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == 259   # STILL_ACTIVE
        kernel32.CloseHandle(h)
        if not alive:
            return "gone"
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == int(pid) and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # без владельца
                found.append(hwnd)
            return True

        user32.EnumWindows(each, 0)
        if not found:
            return "none"
        return "hung" if any(user32.IsHungAppWindow(w) for w in found) else "ok"
    except Exception:  # noqa: BLE001 — не смогли проверить: окно закроется по времени
        return "ok"


class _PageApi:
    """Методы, которые вызывает кнопка «Отмена» на странице (pywebview.api.cancel)."""

    def __init__(self, on_cancel):
        self._on_cancel = on_cancel

    def cancel(self) -> bool:
        self._on_cancel(True)     # кнопка: «Отмена» до запуска, «Закрыть игру» — когда игра уже идёт
        return True


class LaunchWindow:
    def __init__(self, page: Path, on_cancel):
        self._page = page
        self._on_cancel = on_cancel
        self._lock = threading.Lock()
        self._win = None
        self._ready = False
        self._dirty = False
        self._opened = 0.0
        self._lines: list[str] = []
        self._snap = {"state": "preparing", "value": 0.0, "text": "", "title": "", "lang": "ru"}

    # ---- открыть / закрыть ----
    def open(self, title: str, lang: str, parent=None) -> None:
        import webview   # импорт здесь: модуль нужен только в окне pywebview
        self.close()
        with self._lock:
            self._snap = {"state": "preparing", "value": 0.0, "text": "Проверка модов...", "title": title, "lang": lang}
            self._lines = []
            self._dirty = True
            self._ready = False
        w, h = 600, 520
        pos = {}
        if parent is not None:   # по центру окна лаунчера, а не где попало на экране
            try:
                pos = {"x": int(parent.x + (parent.width - w) / 2), "y": int(parent.y + (parent.height - h) / 2)}
            except Exception:  # noqa: BLE001 — размеры окна неизвестны: пусть система сама решит
                pos = {}
        win = webview.create_window(
            "Подождите… — VaultMC", str(self._page), js_api=_PageApi(self._on_cancel),
            width=w, height=h, resizable=False, background_color="#0c0f16", **pos,
        )
        win.events.loaded += self._on_loaded
        win.events.closing += lambda: self._on_closing(win)
        with self._lock:
            self._win = win
            self._opened = time.monotonic()
        threading.Thread(target=self._pump, args=(win,), daemon=True).start()

    def close(self) -> None:
        with self._lock:
            win, self._win = self._win, None   # окно уже не «текущее»: его closing — не отмена (см. _on_closing)
        if win is not None:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001 — окно уже закрыто
                pass

    def close_soft(self, pid_getter=None, min_total: float = 3.5, max_total: float = 120.0) -> None:
        """Закрыть окно, когда игра уже показала своё окно и оно отвечает (не «не отвечает»).

        Пока окно игры висит, Windows пишет «не отвечает» и по клику показывает вопрос про закрытие программы —
        наше окно остаётся сверху и закрывается позже. Не раньше min_total с открытия (быстрый запуск должен быть
        виден) и не позже max_total (на всякий случай)."""
        with self._lock:
            win = self._win
            opened = self._opened
        if win is None:
            return

        def later():
            time.sleep(1.2)                       # «Игра запущена» видно хотя бы 1,2 с
            ok_in_row = 0
            while True:
                with self._lock:
                    if self._win is not win:
                        return
                age = time.monotonic() - opened
                state = _game_window_state(pid_getter() if pid_getter else None)
                if state == "gone":               # игра уже закрылась
                    break
                ok_in_row = ok_in_row + 1 if state == "ok" else 0
                if (age >= min_total and ok_in_row >= 3) or age >= max_total:
                    break
                time.sleep(0.4)
            self.close()
        threading.Thread(target=later, daemon=True).start()

    def is_open(self) -> bool:
        with self._lock:
            return self._win is not None

    # ---- данные из запуска ----
    def update(self, event: str, payload) -> None:
        """Вызывается из потоков запуска: только копим данные, отправка — в _pump."""
        with self._lock:
            if self._win is None:
                return
            if event == "log":
                for raw in str(payload).split("\n"):
                    if raw.strip():
                        self._lines.append(raw.rstrip())
                if len(self._lines) > 400:
                    self._lines = self._lines[-400:]
            elif event == "progress":
                value, text = payload
                self._snap["value"] = float(value)
                self._snap["text"] = str(text)
            elif event == "state":
                self._snap["state"] = str(payload)
            elif event == "error":
                self._lines.append(f"[Ошибка] {payload}")
                self._snap["text"] = str(payload)
            self._dirty = True

    # ---- внутреннее ----
    def _on_loaded(self) -> None:
        with self._lock:
            self._ready = True
            self._dirty = True

    def _on_closing(self, win):
        # закрываем сами (close) — окно к этому моменту уже не текущее. Раньше это отмечал флаг, и если Windows
        # не присылала closing на destroy, флаг оставался и следующий крестик игрока не отменял запуск
        with self._lock:
            if self._win is not win:
                return None
            self._win = None
        self._on_cancel(False)   # крестик — отмена запуска (уже запущенную игру не закрывает)
        return None

    def _pump(self, win) -> None:
        while True:
            time.sleep(0.15)
            with self._lock:
                if self._win is not win:
                    return
                if not (self._ready and self._dirty):
                    continue
                data = dict(self._snap)
                data["lines"] = self._lines
                self._lines = []
                self._dirty = False
            js = f"window.__launch && window.__launch({json.dumps(data, ensure_ascii=False)})"
            try:
                win.evaluate_js(js)
            except Exception:  # noqa: BLE001 — окно закрылось на ходу
                return
