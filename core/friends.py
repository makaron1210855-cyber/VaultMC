"""
Друзья и совместная игра без Radmin/Hamachi.

Как это работает: в сборку хоста ставится мод-туннель (e4all или e4mc). Хост открывает мир
«Открыть для сети», мод выдаёт публичный адрес, лаунчер ловит его в логе игры и показывает
с кнопкой «Копировать». Друг вставляет адрес — лаунчер сам запускает игру и подключается.

Своего сервера у лаунчера нет, поэтому друзья обмениваются «кодом друга» (ник + адрес)
через любой мессенджер. Код — это обычный текст VAULT-..., его можно вставить в «Добавить друга».
"""

from __future__ import annotations

import base64
import json
import os
import threading
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .utils import LAUNCHER_HOME

# Адрес комнаты, который выдают моды-туннели e4mc и e4all: «abcde.e4mc.link» (иногда с портом).
_TUNNEL = re.compile(r"\b((?:[a-z0-9-]+\.)+e4mc\.link)(?::(\d{2,5}))?\b", re.IGNORECASE)
# Свой домен e4all (если появится): только в строках, где упомянут сам мод, и только «похожие на сайт» окончания
_MOD_LINE = re.compile(r"\b(e4mc|e4all)\b", re.IGNORECASE)
_OTHER = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:link|net|com|org|io|gg|dev|me|xyz|ru|de))(?::(\d{2,5}))?\b", re.IGNORECASE)
_JAVA_PREFIXES = ("com.", "net.", "org.", "io.", "dev.", "me.", "de.", "ru.")
_IGNORED = ("minecraft.net", "mojang.com", "modrinth.com", "github.com", "ely.by", "curseforge.com",
            "fabricmc.net", "discord.gg", "e4mc.link")


# Служебные строки e4mc: «broker resp: {"id":"uk","host":"uk.e4mc.link",...}», «using relay uk», «relaymap resp: ...».
# В них адрес сервера-посредника e4mc, а не комнаты — раньше лаунчер принимал его за адрес комнаты.
_SERVICE_LINE = re.compile(r"broker|relay", re.IGNORECASE)


def find_tunnel_address(line: str) -> str | None:
    if _SERVICE_LINE.search(line):
        return None
    match = _TUNNEL.search(line)
    if match and match.group(1).lower() not in ("e4mc.link", "broker.e4mc.link", "status.e4mc.link"):
        return match.group(1).lower() + (f":{match.group(2)}" if match.group(2) else "")
    if not _MOD_LINE.search(line):
        return None
    for match in _OTHER.finditer(line):
        host = match.group(1).lower()
        if host.startswith(_JAVA_PREFIXES) or any(host == x or host.endswith("." + x) for x in _IGNORED):
            continue  # имена классов Java (com.mojang...) и известные сайты — не адрес комнаты
        return host + (f":{match.group(2)}" if match.group(2) else "")
    return None


@dataclass
class Friend:
    name: str
    note: str = ""
    address: str = ""
    added_at: float = field(default_factory=time.time)


class FriendStore:
    def __init__(self, path: Path = LAUNCHER_HOME / "friends.json"):
        self.path = path
        try:
            self.friends = [Friend(**f) for f in json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, json.JSONDecodeError, TypeError):
            self.friends = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps([asdict(f) for f in self.friends], indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def upsert(self, name: str, address: str = "", note: str = "") -> Friend:
        for f in self.friends:
            if f.name.lower() == name.lower():
                f.address = address or f.address
                f.note = note or f.note
                self.save()
                return f
        friend = Friend(name, note, address)
        self.friends.append(friend)
        self.save()
        return friend

    def remove(self, friend: Friend) -> None:
        self.friends = [f for f in self.friends if f is not friend]
        self.save()


def make_friend_code(name: str, address: str = "") -> str:
    raw = json.dumps({"n": name, "a": address}, separators=(",", ":")).encode()
    return "VAULT-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_friend_code(code: str) -> tuple[str, str]:
    code = code.strip()
    upper = code.upper()
    if upper.startswith("VAULT-"):
        body = code[6:]
    elif upper.startswith("NEON-"):  # коды прежней версии
        body = code[5:]
    else:
        raise ValueError("Код друга начинается с VAULT-")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    return data["n"], data.get("a", "")
