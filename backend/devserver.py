"""
Режим разработки: интерфейс открывается в обычном браузере, а вызовы api.* идут сюда по HTTP.
Удобно править HTML/CSS/JS: обновили страницу (F5) — увидели результат.

    python main.py --dev     ->  http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from core.utils import APP_BUILD, APP_VERSION, resource_path

from .api import Api
from .events import EventBus


def serve(port: int = 8765, open_browser: bool = True, auto_exit: bool = False) -> None:
    """auto_exit: завершить процесс, когда окно браузера закрыто (интерфейс перестал спрашивать события)."""
    bus = EventBus()
    api = Api(bus)
    web_root = resource_path("web")
    assets_root = resource_path("assets")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def translate_path(self, path):
            if path.startswith("/assets/"):
                return str(assets_root / path[len("/assets/"):].split("?")[0])
            return str(web_root / path.lstrip("/").split("?")[0]) if path not in ("/", "") else str(web_root / "index.html")

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")  # браузер не держит старые файлы интерфейса
            super().end_headers()

        def _json(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/version":  # чтобы новая версия отличала себя от старых копий
                return self._json(200, {"version": f"{APP_VERSION}+{APP_BUILD}", "pid": os.getpid()})
            if self.path == "/shutdown":
                api._save()
                self._json(200, {"ok": True})
                threading.Timer(0.2, lambda: os._exit(0)).start()
                return
            if self.path == "/events":
                last_seen[0] = time.time()
                return self._json(200, bus.pull())
            return super().do_GET()

        def do_POST(self):
            if not self.path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            name = self.path[5:]
            method = getattr(api, name, None)
            if name.startswith("_") or method is None:
                return self._json(404, {"error": f"нет метода {name}"})
            args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
            try:
                self._json(200, {"result": method(*args)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})

    last_seen = [time.time() + 30]  # 30 с на открытие окна

    def watchdog():
        while True:
            time.sleep(3)
            if time.time() - last_seen[0] > 150:  # свернутое окно браузер «будит» редко
                api._save()
                # окно закрыли: друзья видят «не в сети» сразу, а не через 40 минут (как в main.py для обычного окна)
                t = threading.Thread(target=api.presence_offline, daemon=True)
                t.start()
                t.join(5)
                os._exit(0)

    if auto_exit:
        threading.Thread(target=watchdog, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"VaultMC (dev): http://127.0.0.1:{port}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    server.serve_forever()
