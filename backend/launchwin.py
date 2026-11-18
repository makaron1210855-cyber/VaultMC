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


class _PageApi:
    """Методы, которые вызывает кнопка «Отмена» на странице (pywebview.api.cancel)."""

    def __init__(self, on_cancel):
        self._on_cancel = on_cancel

    def cancel(self) -> bool:
        self._on_cancel()
        return True


class LaunchWindow:
    def __init__(self, page: Path, on_cancel):
        self._page = page
        self._on_cancel = on_cancel
        self._lock = threading.Lock()
        self._win = None
        self._ready = False
        self._dirty = False
        self._by_us = False           # закрываем сами (игра запустилась) — это не отмена
        self._lines: list[str] = []
        self._snap = {"state": "preparing", "value": 0.0, "text": "", "title": "", "lang": "ru"}

    # ---- открыть / закрыть ----
    def open(self, title: str, lang: str) -> None:
        import webview   # импорт здесь: модуль нужен только в окне pywebview
        self.close()
        with self._lock:
            self._snap = {"state": "preparing", "value": 0.0, "text": "Проверка модов...", "title": title, "lang": lang}
            self._lines = []
            self._dirty = True
            self._ready = False
        win = webview.create_window(
            "Подождите… — VaultMC", str(self._page), js_api=_PageApi(self._on_cancel),
            width=540, height=430, resizable=False, background_color="#0c0f16",
        )
        win.events.loaded += self._on_loaded
        win.events.closing += self._on_closing
        with self._lock:
            self._win = win
        threading.Thread(target=self._pump, args=(win,), daemon=True).start()

    def close(self) -> None:
        with self._lock:
            win, self._win = self._win, None
            if win is not None:
                self._by_us = True
        if win is not None:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001 — окно уже закрыто
                pass

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

    def _on_closing(self):
        with self._lock:
            if self._by_us:
                self._by_us = False
                return None
            self._win = None
        self._on_cancel()   # крестик — тоже отмена запуска
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
