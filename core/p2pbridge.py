"""
Мост для прямого подключения к другу (как в XMCL): игра ⇄ TCP ⇄ этот мост ⇄ WebSocket ⇄ WebRTC в окне лаунчера ⇄ друг.

WebRTC (обход NAT, шифрование) делает встроенный браузер окна (WebView2), а Python только перекладывает байты
между игрой и окном через локальный WebSocket на 127.0.0.1. Сторонних библиотек нет — свой маленький WebSocket-сервер.

Пути WebSocket (все с секретным токеном, чтобы чужие сайты в браузере не могли подключиться):
  /<token>/ctl         — управление: мост сообщает окну о новых подключениях игры (гость)
  /<token>/host/<id>   — канал хозяина: мост открывает TCP к открытому для сети миру
  /<token>/guest/<id>  — канал гостя: мост отдаёт сюда подключение, которое игра сделала к 127.0.0.1:<порт>
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WsClosed(Exception):
    pass


class _Ws:
    """Серверная сторона одного WebSocket-соединения (RFC 6455, только то, что нужно)."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.lock = threading.Lock()
        self.closed = False

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise WsClosed()
            buf += chunk
        return buf

    def recv(self) -> tuple[int, bytes]:
        """Следующее сообщение: (1 — текст | 2 — байты, данные). Пинги отвечаем сами."""
        parts, first_op = [], None
        while True:
            b1, b2 = self._read(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > 64 * 1024 * 1024:
                raise WsClosed()
            mask = self._read(4) if b2 & 0x80 else b""
            data = self._read(n) if n else b""
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data)) if n < 64 else _unmask(data, mask)
            if op == 8:
                raise WsClosed()
            if op == 9:
                self.send(data, 0xA)
                continue
            if op == 0xA:
                continue
            if op != 0:
                first_op = op
            parts.append(data)
            if fin:
                return first_op or 2, b"".join(parts)

    def send(self, data: bytes | str, op: int | None = None) -> None:
        if isinstance(data, str):
            data, op = data.encode(), op or 1
        op = op or 2
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536
                                     else bytes([127]) + struct.pack(">Q", n))
        with self.lock:
            if self.closed:
                raise WsClosed()
            self.sock.sendall(head + data)

    def close(self) -> None:
        if self.closed:
            return
        try:
            with self.lock:
                self.sock.sendall(b"\x88\x00")
        except OSError:
            pass
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


def _unmask(data: bytes, mask: bytes) -> bytes:
    """Быстрое снятие маски для больших кадров (через целые числа, без цикла по байтам)."""
    n = len(data)
    key = int.from_bytes(mask * (n // 4 + 1), "little") & ((1 << (8 * n)) - 1)
    return (int.from_bytes(data, "little") ^ key).to_bytes(n, "little")


class P2PBridge:
    def __init__(self, log=print):
        self.log = log
        self.token = secrets.token_hex(12)
        self.lan_port = 0                 # хозяин: порт мира, открытого для сети
        self.guest_port = 0               # гость: куда подключается игра
        self._srv: socket.socket | None = None
        self._guest_srv: socket.socket | None = None
        self._ctl: _Ws | None = None
        self._pending: dict[str, socket.socket] = {}
        self._next = 0
        self._lock = threading.Lock()
        self.port = 0

    # ------------------------------------------------------------ WebSocket-сервер для окна
    def start(self) -> int:
        if self._srv:
            return self.port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(32)
        self._srv, self.port = srv, srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()
        return self.port

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/{self.token}"

    def _accept_loop(self, srv: socket.socket) -> None:
        while True:
            try:
                sock, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(sock,), daemon=True).start()

    def _handshake(self, sock: socket.socket) -> str | None:
        sock.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk or len(data) > 16384:
                return None
            data += chunk
        lines = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
        try:
            method, path, _ = lines[0].split(" ", 2)
        except ValueError:
            return None
        headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
        key = headers.get("sec-websocket-key")
        if method != "GET" or not key or not path.startswith(f"/{self.token}/"):
            sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return None
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        sock.settimeout(None)
        return path[len(self.token) + 2:]

    def _handle(self, sock: socket.socket) -> None:
        try:
            route = self._handshake(sock)
        except OSError:
            route = None
        if route is None:
            sock.close()
            return
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ws = _Ws(sock)
        kind, _, cid = route.partition("/")
        if kind == "ctl":
            if self._ctl:
                self._ctl.close()
            self._ctl = ws
            try:
                while True:
                    ws.recv()                     # окно ничего не шлёт — просто держим соединение
            except (WsClosed, OSError):
                pass
            ws.close()
            if self._ctl is ws:
                self._ctl = None
        elif kind == "host":
            if not self.lan_port:
                ws.close()
                return
            try:
                game = socket.create_connection(("127.0.0.1", self.lan_port), timeout=8)
            except OSError as exc:
                self.log(f"[Прямое подключение] Мир не отвечает на порту {self.lan_port}: {exc}")
                ws.close()
                return
            self.log("[Прямое подключение] Друг подключился")
            self._pump(ws, game)
        elif kind == "guest":
            with self._lock:
                game = self._pending.pop(cid, None)
            if game is None:
                ws.close()
                return
            self._pump(ws, game)
        else:
            ws.close()

    def _pump(self, ws: _Ws, tcp: socket.socket) -> None:
        tcp.settimeout(None)
        tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def tcp_to_ws():
            try:
                while True:
                    data = tcp.recv(65536)
                    if not data:
                        break
                    ws.send(data)
            except (OSError, WsClosed):
                pass
            ws.close()

        threading.Thread(target=tcp_to_ws, daemon=True).start()
        try:
            while True:
                _op, data = ws.recv()
                tcp.sendall(data)
        except (OSError, WsClosed):
            pass
        ws.close()
        try:
            tcp.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        tcp.close()

    # ------------------------------------------------------------ гость: порт для игры
    def guest_listen(self) -> int:
        if self._guest_srv:
            return self.guest_port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self._guest_srv, self.guest_port = srv, srv.getsockname()[1]
        threading.Thread(target=self._guest_loop, args=(srv,), daemon=True).start()
        return self.guest_port

    def _guest_loop(self, srv: socket.socket) -> None:
        while True:
            try:
                game, _ = srv.accept()
            except OSError:
                return
            with self._lock:
                self._next += 1
                cid = f"g{self._next}"
                self._pending[cid] = game
            ctl = self._ctl
            if ctl is None:
                self.log("[Прямое подключение] Окно лаунчера не на связи — подключение игры сброшено")
                self._forget(cid)
                game.close()
                continue
            try:
                ctl.send(json.dumps({"t": "conn", "id": cid}))
            except (OSError, WsClosed):
                self._forget(cid)
                game.close()
                continue
            threading.Timer(15, self._drop_pending, args=(cid,)).start()   # окно так и не открыло канал

    def _forget(self, cid: str) -> None:
        """Убрать из ожидания закрытое подключение (иначе закрытые сокеты копились бы в памяти)."""
        with self._lock:
            self._pending.pop(cid, None)

    def _drop_pending(self, cid: str) -> None:
        with self._lock:
            game = self._pending.pop(cid, None)
        if game is not None:
            self.log("[Прямое подключение] Соединение с другом прервано — игра не смогла подключиться")
            game.close()

    def stop_guest(self) -> None:
        if self._guest_srv:
            try:
                self._guest_srv.close()
            except OSError:
                pass
        self._guest_srv, self.guest_port = None, 0

