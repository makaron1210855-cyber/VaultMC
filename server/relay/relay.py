#!/usr/bin/env python3
"""Ретранслятор VaultMC (запускается на VPS, только стандартная библиотека Python 3.8+).

Каждое соединение на CONTROL_PORT начинается с одной строки:
  HOST2 <ключ> [порт]  — хозяин открывает комнату с ключом (32 знака 0-9a-f). Можно попросить прежний порт:
                         после обрыва связи адрес у гостей остаётся тем же. Ответ «PORT <n>», дальше сервер
                         шлёт «PING» раз в 20 с и «NEW <id>» на каждого гостя.
  JOIN <порт> <ключ>   — гость (через свой лаунчер). Ответ «OK» — дальше байты игры как есть; «ERR <причина>» — отказ.
  CHECK <порт> <ключ>  — проверить комнату, не подключаясь: «OK» или «ERR <причина>».
  DATA <id>            — хозяин открывает соединение для гостя <id>.
  PING                 — ответ «PONG» (лаунчер меряет задержку).
  HOST                 — старый способ (лаунчеры до 6.4.14): комната без ключа, гости заходят прямо на публичный
                         порт. Временно, пока все не обновятся; убрать после 1.0.

Публичные порты PORT_MIN..PORT_MAX пускают гостей только в комнаты старого способа. В комнату с ключом
без ключа не попасть — перебор портов ничего не даёт.
Причины отказа: gone — комнаты нет; key — неверный ключ; host — хозяин не ответил; limit — много попыток.
"""
import asyncio
import hmac
import re
import secrets
import socket
import time
from collections import defaultdict, deque

CONTROL_PORT = 7000
PORT_MIN, PORT_MAX = 25600, 25999    # открыть в файрволе
MAX_ROOMS = 300
HOSTS_PER_IP = 3                      # комнат одновременно с одного адреса
JOINS_PER_MIN = 30                    # подключений гостей в минуту с одного адреса
PAIR_TIMEOUT = 15                     # сек. ждём, пока хозяин откроет DATA-соединение
PING_EVERY = 20                       # сек. — keepalive управляющего соединения
FIRST_LINE_TIMEOUT = 15

KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class Room:
    def __init__(self, writer, token, ip):
        self.writer = writer
        self.token = token            # None — комната старого способа (без ключа)
        self.ip = ip


rooms = {}          # публичный порт -> Room
pending = {}        # id -> Future с (reader, writer) хозяйского DATA-соединения
joins = defaultdict(deque)   # адрес гостя -> время последних подключений


def log(text):
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def keepalive(writer):
    """Мёртвые соединения (у игрока пропал интернет) иначе висели бы вечно."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 20), ("TCP_KEEPCNT", 3)):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def peer_ip(writer):
    peer = writer.get_extra_info("peername")
    return peer[0] if peer else ""


def free_port(prefer=None):
    if prefer is not None and PORT_MIN <= prefer <= PORT_MAX and prefer not in rooms:
        return prefer
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p not in rooms:
            return p
    return None


def same_key(a, b):
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def too_many_joins(ip):
    now = time.monotonic()
    q = joins[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= JOINS_PER_MIN:
        return True
    q.append(now)
    return False


async def say(writer, text):
    writer.write((text + "\n").encode())
    await writer.drain()


def close(writer):
    try:
        writer.close()
    except Exception:  # noqa: BLE001
        pass


async def pipe(src, dst):
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError):
        pass
    finally:
        close(dst)


async def join(a, b):
    ar, aw = a
    br, bw = b
    await asyncio.gather(pipe(ar, bw), pipe(br, aw))


async def pair(room, reader, writer, greet):
    """Гость пришёл: просим хозяина открыть DATA-соединение и соединяем их. greet — ответ гостю перед данными."""
    sid = secrets.token_hex(16)
    fut = asyncio.get_running_loop().create_future()
    pending[sid] = fut
    try:
        await say(room.writer, f"NEW {sid}")
        hr, hw = await asyncio.wait_for(fut, PAIR_TIMEOUT)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pending.pop(sid, None)
        if greet:
            try:
                await say(writer, "ERR host")
            except (ConnectionError, OSError):
                pass
        close(writer)
        return
    if greet:
        await say(writer, "OK")
    await join((reader, writer), (hr, hw))


async def host_room(writer, token, want_port, ip):
    """Управляющее соединение хозяина: выдаём порт и держим комнату, пока соединение живо."""
    port = None
    old = rooms.get(want_port) if want_port is not None else None
    if token and old is not None and same_key(old.token, token):
        port = want_port              # тот же хозяин переподключился — отдаём ему прежний порт
        close(old.writer)
    else:
        mine = sum(1 for r in rooms.values() if r.ip == ip)
        if len(rooms) >= MAX_ROOMS or mine >= HOSTS_PER_IP:
            await say(writer, "ERR limit")
            close(writer)
            return
        port = free_port(want_port)
        if port is None:
            await say(writer, "ERR busy")
            close(writer)
            return
    room = Room(writer, token, ip)
    rooms[port] = room
    await say(writer, f"PORT {port}")
    log(f"комната на порту {port}{' (без ключа)' if token is None else ''}")
    try:
        while True:
            await asyncio.sleep(PING_EVERY)
            await say(writer, "PING")
    except (ConnectionError, OSError):
        pass
    finally:
        if rooms.get(port) is room:   # порт мог уже перейти к переподключившемуся хозяину
            rooms.pop(port, None)
            log(f"комната на порту {port} закрыта")
        close(writer)


def find_room(port_text, token):
    """(комната, причина отказа)."""
    try:
        port = int(port_text)
    except ValueError:
        return None, "gone"
    room = rooms.get(port)
    if room is None or room.token is None:
        return None, "gone"
    if not same_key(room.token, token):
        return None, "key"
    return room, ""


async def on_control(reader, writer):
    keepalive(writer)
    ip = peer_ip(writer)
    try:
        line = await asyncio.wait_for(reader.readline(), FIRST_LINE_TIMEOUT)
    except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
        close(writer)
        return
    parts = line.decode("ascii", "replace").split()
    cmd = parts[0] if parts else ""
    try:
        if cmd == "HOST2" and len(parts) in (2, 3) and KEY_RE.match(parts[1]):
            want = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
            await host_room(writer, parts[1], want, ip)
        elif cmd == "HOST" and len(parts) == 1:
            await host_room(writer, None, None, ip)
        elif cmd in ("JOIN", "CHECK") and len(parts) == 3:
            if too_many_joins(ip):
                await say(writer, "ERR limit")
                close(writer)
                return
            room, why = find_room(parts[1], parts[2])
            if room is None:
                await say(writer, f"ERR {why}")
                close(writer)
            elif cmd == "CHECK":
                await say(writer, "OK")
                close(writer)
            else:
                await pair(room, reader, writer, greet=True)
        elif cmd == "DATA" and len(parts) == 2:
            fut = pending.pop(parts[1], None)
            if fut is None or fut.done():
                close(writer)
                return
            fut.set_result((reader, writer))   # дальше соединение ведёт обработчик гостя
        elif cmd == "PING":
            await say(writer, "PONG")
            close(writer)
        else:
            close(writer)
    except (ConnectionError, OSError):
        close(writer)


async def on_public(port, reader, writer):
    """Гость подключился прямо к публичному порту — так можно только в комнату старого способа."""
    keepalive(writer)
    room = rooms.get(port)
    if room is None or room.token is not None:
        close(writer)
        return
    await pair(room, reader, writer, greet=False)


async def main():
    control = await asyncio.start_server(on_control, "0.0.0.0", CONTROL_PORT)
    log(f"ретранслятор: управление {CONTROL_PORT}, комнаты {PORT_MIN}-{PORT_MAX}")
    servers = [control]

    # Публичные порты открываем сразу для всего диапазона; порт без комнаты старого способа сразу закрывает подключение.
    async def open_public(p):
        async def handler(r, w):
            await on_public(p, r, w)
        return await asyncio.start_server(handler, "0.0.0.0", p)

    for p in range(PORT_MIN, PORT_MAX + 1):
        try:
            servers.append(await open_public(p))
        except OSError as exc:
            log(f"порт {p} занят: {exc}")
    async with control:
        await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
