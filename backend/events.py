"""
Доставка событий из Python в интерфейс (JS).

Фоновые потоки вызывают bus.emit(event, payload). События копятся в очереди и
отправляются пачкой раз в 80 мс — так поток логов игры не нагружает интерфейс.

push-режим (pywebview): вызываем window.__vaultEvents([...]) через evaluate_js.
pull-режим (браузер для разработки): интерфейс сам забирает события через /events.
"""

from __future__ import annotations

import json
import queue
import threading
import time


class EventBus:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._window = None
        self._pull_buffer: list = []
        self._lock = threading.Lock()
        self._gaming = False
        threading.Thread(target=self._pump, daemon=True).start()

    def attach_window(self, window) -> None:
        self._window = window

    def set_gaming(self, value: bool) -> None:
        """Пока идёт игра, строки лога отправляем в окно реже (раз в 0,5 с) — меньше работы окну."""
        self._gaming = bool(value)

    def emit(self, event: str, payload=None) -> None:
        self._queue.put((event, payload))

    def pull(self) -> list:
        with self._lock:
            items, self._pull_buffer = self._pull_buffer, []
        return items

    def _pump(self) -> None:
        held: list = []          # строки лога, отложенные до следующей отправки (во время игры)
        last_log_push = 0.0
        while True:
            # ждём первое событие, не просыпаясь впустую: в простое поток спит и процессор не трогает.
            # Отложенный лог (во время игры) — ждём не дольше, чем до его отправки.
            wait = None if not held else max(0.0, 0.5 - (time.monotonic() - last_log_push))
            try:
                first = self._queue.get(timeout=wait)
            except queue.Empty:
                first = None
            if first is not None:
                time.sleep(0.08)                 # копим пачку: строки лога приходят очередями
            batch, logs = [], held
            held = []
            pending = [first] if first is not None else []
            try:
                while len(batch) < 400:
                    event, payload = pending.pop() if pending else self._queue.get_nowait()
                    if event == "log":          # склеиваем строки лога в одно событие
                        logs.append(str(payload))
                        continue
                    if logs:
                        batch.append({"e": "log", "p": logs})
                        logs = []
                    batch.append({"e": event, "p": payload})
            except queue.Empty:
                pass
            now = time.monotonic()
            if logs:
                if self._gaming and not batch and now - last_log_push < 0.5:
                    held = logs[-5000:]          # только лог — подождём и отправим пачкой
                else:
                    batch.append({"e": "log", "p": logs[-5000:]})
                    last_log_push = now
            if not batch:
                continue
            if self._window is not None:
                script = f"window.__vaultEvents && window.__vaultEvents({json.dumps(batch, ensure_ascii=False)})"
                try:
                    # run_js выполняет код как есть: без eval и экранирования строки —
                    # заметно легче для окна, когда игра пишет много строк
                    run = getattr(self._window, "run_js", None) or self._window.evaluate_js
                    run(script)
                except Exception:  # noqa: BLE001 — окно закрывается
                    pass
            else:
                with self._lock:
                    self._pull_buffer.extend(batch)
                    del self._pull_buffer[:-2000]
