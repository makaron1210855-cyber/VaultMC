"""Клиент ретранслятора VaultMC: хозяин держит соединение с VPS, друзья заходят на публичный порт сервера.

Протокол — см. server/relay/relay.py. Здесь: открыть комнату, переслать каждого друга в локальный мир.
"""
from __future__ import annotations

import socket
import threading
import time

RELAY_HOST = "13.143.207.131"   # VPS в Москве
CONTROL_PORT = 7000


class RelayHost:
    def __init__(self, local_port: int, log, on_port):
        self.local_port = local_port
        self.log = log
        self.on_port = on_port          # вызывается с новым публичным портом (и при переподключении)
        self.public_port = 0
        self.alive = True
        self._ready = threading.Event()
        self._ctl: socket.socket | None = None

    def start(self, timeout: float = 10.0) -> int:
        threading.Thread(target=self._run, daemon=True).start()
        if not self._ready.wait(timeout) or not self.public_port:
            self.stop()
            raise OSError("ретранслятор не ответил")
        return self.public_port

    def stop(self) -> None:
        self.alive = False
        try:
            if self._ctl:
                self._ctl.close()
        except OSError:
            pass

    def _run(self) -> None:
        while self.alive:
            try:
                self._session()
            except OSError as exc:
                self.log(f"[Комната] ретранслятор: {exc}")
            if self.alive:
                time.sleep(3)

    def _session(self) -> None:
        ctl = socket.create_connection((RELAY_HOST, CONTROL_PORT), timeout=15)
        self._ctl = ctl
        ctl.sendall(b"HOST\n")
        f = ctl.makefile("rb")
        first = f.readline().decode("ascii", "replace").strip()
        if not first.startswith("PORT "):
            raise OSError("сервер не выдал порт комнаты")
        port = int(first[5:])
        self.public_port = port
        self._ready.set()
        self.log(f"[Комната] ретранслятор открыл порт {port}")
        self.on_port(port)
        ctl.settimeout(60)              # сервер шлёт PING каждые 20 с — молчание больше минуты значит обрыв
        while self.alive:
            line = f.readline()
            if not line:
                break
            if line.startswith(b"NEW "):
                sid = line[4:].strip().decode("ascii", "replace")
                threading.Thread(target=self._serve_friend, args=(sid,), daemon=True).start()
        self.log("[Комната] связь с ретранслятором оборвалась, переподключаюсь")

    def _serve_friend(self, sid: str) -> None:
        try:
            data = socket.create_connection((RELAY_HOST, CONTROL_PORT), timeout=10)
            data.sendall(f"DATA {sid}\n".encode())
            local = socket.create_connection(("127.0.0.1", self.local_port), timeout=10)
        except OSError as exc:
            self.log(f"[Комната] друг не подключился: {exc}")
            return
        data.settimeout(None)
        local.settimeout(None)
        for s in (data, local):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        t = threading.Thread(target=_pipe, args=(data, local), daemon=True)
        t.start()
        _pipe(local, data)
        t.join(1)


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            chunk = a.recv(65536)
            if not chunk:
                break
            b.sendall(chunk)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
