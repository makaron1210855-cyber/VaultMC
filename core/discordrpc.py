"""
Статус «Играет в VaultMC» в Discord (Rich Presence).

Discord на Windows слушает именованные каналы \\\\.\\pipe\\discord-ipc-0..9. Протокол простой:
каждое сообщение = заголовок (opcode, длина; little-endian uint32) + JSON.
Лаунчер только ставит и снимает статус; если Discord не запущен — тихо ничего не делает.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import uuid

from .utils import is_windows

# Application ID приложения VaultMC в Discord Developer Portal (публичный идентификатор, не секрет)
CLIENT_ID = "1572527959338655805"

OP_HANDSHAKE = 0
OP_FRAME = 1
HEADER = struct.Struct("<II")


def _open_pipe():
    """Открывает первый доступный канал Discord, либо None."""
    for i in range(10):
        try:
            return open(rf"\\.\pipe\discord-ipc-{i}", "r+b", buffering=0)
        except OSError:
            continue
    return None


def _send(pipe, opcode: int, payload: dict) -> dict | None:
    data = json.dumps(payload).encode("utf-8")
    pipe.write(HEADER.pack(opcode, len(data)) + data)
    head = pipe.read(HEADER.size)
    if len(head) < HEADER.size:
        return None
    _, length = HEADER.unpack(head)
    body = pipe.read(length) if length else b""
    try:
        return json.loads(body.decode("utf-8")) if body else None
    except ValueError:
        return None


def _push(activity: dict | None) -> bool:
    """Отправляет статус (или снимает его, если activity=None). True — Discord принял."""
    if not is_windows():
        return False
    pipe = _open_pipe()
    if pipe is None:
        return False
    try:
        _send(pipe, OP_HANDSHAKE, {"v": 1, "client_id": CLIENT_ID})
        reply = _send(pipe, OP_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": str(uuid.uuid4()),
        })
        return bool(reply) and reply.get("evt") != "ERROR"
    except OSError:
        return False
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _run_async(activity: dict | None) -> None:
    # Discord может не ответить — не задерживаем запуск игры
    threading.Thread(target=_push, args=(activity,), daemon=True).start()


def show_playing(instance: str, version: str, started_at: float) -> None:
    """Показывает «Играет в VaultMC» с названием сборки и временем с начала игры."""
    activity = {
        "details": "Играет в VaultMC",
        "state": f"{instance} · {version}"[:128] if instance else f"Minecraft {version}"[:128],
        "timestamps": {"start": int(started_at)},
    }
    _run_async(activity)


def clear() -> None:
    """Убирает статус (после выхода из игры)."""
    _run_async(None)
