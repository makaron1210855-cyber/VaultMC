#!/usr/bin/env python3
"""Ретранслятор VaultMC (запускается на VPS, только стандартная библиотека Python 3.8+).

Как работает:
1. Хозяин открывает мир и подключается к серверу на CONTROL_PORT строкой «HOST».
   Сервер выделяет ему публичный порт и отвечает «PORT <n>».
2. Друг подключается к IP сервера на порт <n> — как к обычному Minecraft-серверу.
3. Сервер просит хозяина по управляющему соединению («NEW <id>»). Хозяин открывает
   новое соединение «DATA <id>», и сервер соединяет его с другом. Дальше байты идут как есть.
"""
import asyncio
import secrets
import time

CONTROL_PORT = 7000
PORT_MIN, PORT_MAX = 25600, 25999    # открыть в файрволе
MAX_ROOMS = 40
PAIR_TIMEOUT = 15                     # сек. ждём, пока хозяин откроет DATA-соединение
PING_EVERY = 20                       # сек. — keepalive управляющего соединения

hosts = {}          # публичный порт -> writer управляющего соединения хозяина
pending = {}        # id -> Future с (reader, writer) хозяйского DATA-соединения


def free_port():
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p not in hosts:
            return p
    return None


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
        try:
            dst.close()
        except Exception:
            pass


async def join(a, b):
    ar, aw = a
    br, bw = b
    await asyncio.gather(pipe(ar, bw), pipe(br, aw))


async def on_control(reader, writer):
    """Хозяин (HOST) или его DATA-соединение."""
    line = await asyncio.wait_for(reader.readline(), 15)
    text = line.decode("ascii", "replace").strip()
    if text == "HOST":
        if len(hosts) >= MAX_ROOMS:
            writer.close()
            return
        port = free_port()
        if port is None:
            writer.close()
            return
        hosts[port] = writer
        writer.write(f"PORT {port}\n".encode())
        await writer.drain()
        print(f"{time.strftime('%H:%M:%S')} комната на порту {port}", flush=True)
        try:
            while True:
                await asyncio.sleep(PING_EVERY)
                writer.write(b"PING\n")
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            hosts.pop(port, None)
            writer.close()
            print(f"{time.strftime('%H:%M:%S')} комната на порту {port} закрыта", flush=True)
    elif text.startswith("DATA "):
        sid = text[5:]
        fut = pending.pop(sid, None)
        if fut is None or fut.done():
            writer.close()
            return
        fut.set_result((reader, writer))   # дальше соединение ведёт обработчик друга
    else:
        writer.close()


async def on_friend(port, reader, writer):
    """Друг подключился к публичному порту комнаты."""
    host = hosts.get(port)
    if host is None:
        writer.close()
        return
    sid = secrets.token_hex(16)
    fut = asyncio.get_running_loop().create_future()
    pending[sid] = fut
    try:
        host.write(f"NEW {sid}\n".encode())
        await host.drain()
        hr, hw = await asyncio.wait_for(fut, PAIR_TIMEOUT)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pending.pop(sid, None)
        writer.close()
        return
    await join((reader, writer), (hr, hw))


async def main():
    control = await asyncio.start_server(on_control, "0.0.0.0", CONTROL_PORT)
    print(f"ретранслятор: управление {CONTROL_PORT}, комнаты {PORT_MIN}-{PORT_MAX}", flush=True)
    servers = [control]

    # Публичные порты открываем сразу для всего диапазона; порт без хозяина сразу закрывает подключение.
    # Так не надо открывать и закрывать порты на лету.
    async def open_room_port(p):
        async def handler(r, w):
            await on_friend(p, r, w)
        return await asyncio.start_server(handler, "0.0.0.0", p)

    for p in range(PORT_MIN, PORT_MAX + 1):
        try:
            servers.append(await open_room_port(p))
        except OSError as exc:
            print(f"порт {p} занят: {exc}", flush=True)
    async with control:
        await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
