"""Клиент ретранслятора VaultMC (VPS). Протокол — см. server/relay/relay.py.

Хозяин (RelayHost) держит соединение с VPS и пересылает каждого гостя в свой мир на 127.0.0.1.
Комната открывается с ключом: гость без ключа не попадёт в мир, даже если переберёт все порты VPS.
Гость (RelayGuest) поднимает у себя адрес 127.0.0.1:<порт> — игра подключается к нему, а мост передаёт
ключ ретранслятору и дальше гонит байты как есть. Ретранслятор старой версии ключей не знает — тогда
хозяин открывает комнату старым способом (публичный порт без ключа), как до 6.4.14.
"""
from __future__ import annotations

import secrets
import socket
import threading
import time

RELAY_HOST = "13.143.207.131"   # VPS в Москве
CONTROL_PORT = 7000

# причины отказа ретранслятора → текст для игрока
REASONS = {
    "gone": "Друг уже закрыл мир",
    "key": "Друг открыл мир заново — нажмите «Зайти» ещё раз или возьмите у него новый код",
    "host": "Друг не ответил — возможно, у него зависла игра",
    "limit": "Слишком много попыток подключения. Подождите минуту",
    "busy": "Ретранслятор VaultMC сейчас перегружен, попробуйте позже",
}


class RelayError(OSError):
    def __init__(self, text: str, reason: str = ""):
        super().__init__(text)
        self.reason = reason


def _line(sock: socket.socket, limit: int = 128) -> str:
    """Одна строка ответа ретранслятора (без буфера: дальше по этому же соединению пойдут байты игры)."""
    out = bytearray()
    while len(out) < limit:
        b = sock.recv(1)
        if not b:
            break
        if b == b"\n":
            break
        out += b
    return out.decode("ascii", "replace").strip()


def _connect(timeout: float = 10.0) -> socket.socket:
    s = socket.create_connection((RELAY_HOST, CONTROL_PORT), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    return s


def _ask(command: str, timeout: float = 10.0) -> tuple[socket.socket, str]:
    s = _connect(timeout)
    try:
        s.sendall((command + "\n").encode())
        return s, _line(s)
    except OSError:
        s.close()
        raise


def _refusal(answer: str) -> RelayError:
    if answer.startswith("ERR "):
        reason = answer[4:].strip()
        return RelayError(REASONS.get(reason, f"Ретранслятор отказал ({reason})"), reason)
    return RelayError("Ретранслятор VaultMC не ответил — возможно, он обновляется", "old" if not answer else "")


class RelayHost:
    def __init__(self, local_port: int, log, on_port, token: str | None = None):
        self.local_port = local_port
        self.log = log
        self.on_port = on_port          # вызывается с публичным портом (и после переподключения)
        self.token = token or secrets.token_hex(16)
        self.secure = True              # False — ретранслятор старой версии, комната без ключа
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

    def _open(self) -> tuple[socket.socket, int]:
        if self.secure:
            want = f" {self.public_port}" if self.public_port else ""
            ctl, first = _ask(f"HOST2 {self.token}{want}", timeout=15)
            if first.startswith("PORT "):
                return ctl, int(first[5:])
            ctl.close()
            if first:                   # новый ретранслятор отказал (лимит, нет мест)
                raise _refusal(first)
            # ретранслятор старой версии закрывает соединение на незнакомую команду — открываем по-старому
            self.secure = False
            self.log("[Комната] ретранслятор старой версии — комната без ключа (как раньше)")
        ctl, first = _ask("HOST", timeout=15)
        if not first.startswith("PORT "):
            ctl.close()
            raise _refusal(first) if first else OSError("сервер не выдал порт комнаты")
        return ctl, int(first[5:])

    def _session(self) -> None:
        ctl, port = self._open()
        self._ctl = ctl
        if self.public_port and port != self.public_port:
            self.log(f"[Комната] ретранслятор выдал новый порт {port} (был {self.public_port})")
        self.public_port = port
        self._ready.set()
        self.log(f"[Комната] ретранслятор открыл порт {port}{'' if self.secure else ' (без ключа)'}")
        self.on_port(port)
        ctl.settimeout(60)              # сервер шлёт PING каждые 20 с — молчание больше минуты значит обрыв
        f = ctl.makefile("rb")
        try:
            while self.alive:
                line = f.readline()
                if not line:
                    break
                if line.startswith(b"NEW "):
                    sid = line[4:].strip().decode("ascii", "replace")
                    threading.Thread(target=self._serve_friend, args=(sid,), daemon=True).start()
        finally:
            f.close()
            ctl.close()
        if self.alive:
            self.log("[Комната] связь с ретранслятором оборвалась, переподключаюсь")

    def _serve_friend(self, sid: str) -> None:
        try:
            data = _connect()
            data.sendall(f"DATA {sid}\n".encode())
            local = socket.create_connection(("127.0.0.1", self.local_port), timeout=10)
        except OSError as exc:
            self.log(f"[Комната] друг не подключился: {exc}")
            return
        _bridge(data, local)


class RelayGuest:
    """Гость: адрес 127.0.0.1:<порт> для игры, каждое подключение игры идёт в комнату друга через ретранслятор."""

    def __init__(self, port: int, token: str, log):
        self.port = int(port)
        self.token = token
        self.log = log
        self.alive = True
        self.local_port = 0
        self.rtt: int | None = None     # мс до ретранслятора, меряется раз в 10 с
        self.rx = self.tx = 0
        self.active = 0                 # сколько подключений игры сейчас идёт
        self.error = ""
        self._srv: socket.socket | None = None
        self._lock = threading.Lock()

    def check(self) -> None:
        """Есть ли такая комната и подходит ли ключ — до запуска игры. Бросает RelayError с понятным текстом."""
        try:
            s, answer = _ask(f"CHECK {self.port} {self.token}")
            s.close()
        except OSError as exc:
            raise RelayError(f"Ретранслятор VaultMC недоступен: {exc}", "down") from None
        if answer != "OK":
            raise _refusal(answer)

    def start(self) -> int:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self._srv = srv
        self.local_port = srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._ping_loop, daemon=True).start()
        return self.local_port

    def stop(self) -> None:
        self.alive = False
        try:
            if self._srv:
                self._srv.close()
        except OSError:
            pass

    def stats(self) -> dict:
        return {"rtt": self.rtt, "rx": self.rx, "tx": self.tx, "active": self.active, "error": self.error}

    def _accept_loop(self) -> None:
        while self.alive:
            try:
                game, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_game, args=(game,), daemon=True).start()

    def _serve_game(self, game: socket.socket) -> None:
        try:
            relay, answer = _ask(f"JOIN {self.port} {self.token}", timeout=20)
        except OSError as exc:
            self._fail(game, f"Ретранслятор VaultMC недоступен: {exc}")
            return
        if answer != "OK":
            relay.close()
            self._fail(game, str(_refusal(answer)))
            return
        self.error = ""
        self.log("[Комната] подключился к другу через ретранслятор")
        with self._lock:
            self.active += 1
        try:
            _bridge(relay, game, self._count)
        finally:
            with self._lock:
                self.active -= 1

    def _fail(self, game: socket.socket, text: str) -> None:
        self.error = text
        self.log(f"[Комната] {text}")
        try:
            game.close()
        except OSError:
            pass

    def _count(self, direction: str, n: int) -> None:
        if direction == "rx":
            self.rx += n
        else:
            self.tx += n

    def _ping_loop(self) -> None:
        while self.alive:
            try:
                s = _connect(timeout=5)
                t0 = time.perf_counter()
                s.sendall(b"PING\n")
                ok = _line(s) == "PONG"
                ms = int((time.perf_counter() - t0) * 1000)
                s.close()
                self.rtt = ms if ok else None
            except OSError:
                self.rtt = None
            for _ in range(10):
                if not self.alive:
                    return
                time.sleep(1)


def _bridge(remote: socket.socket, local: socket.socket, count=None) -> None:
    """Гоним байты в обе стороны, пока одна из сторон не закроется. count(«rx»|«tx», байт) — для счётчиков."""
    for s in (remote, local):
        s.settimeout(None)
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    t = threading.Thread(target=_pipe, args=(remote, local, count, "rx"), daemon=True)
    t.start()
    _pipe(local, remote, count, "tx")
    t.join(5)
    for s in (remote, local):      # раньше сокеты только «выключались» и копились до закрытия лаунчера
        try:
            s.close()
        except OSError:
            pass


def _pipe(a: socket.socket, b: socket.socket, count=None, direction: str = "") -> None:
    try:
        while True:
            chunk = a.recv(65536)
            if not chunk:
                break
            b.sendall(chunk)
            if count:
                count(direction, len(chunk))
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
