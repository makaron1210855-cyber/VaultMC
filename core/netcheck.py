"""Проверка сети: открываются ли сайты, которые нужны лаунчеру, и как быстро. Запускается только по кнопке.

«Открывается» = сайт ответил хоть каким-то HTTP-кодом (даже 403/404 — главное, что связь есть). Отдельно различаем:
  • DNS провайдера не находит адрес (а через Cloudflare DNS-over-HTTPS адрес существует) — лечится DNS 1.1.1.1 или VPN;
  • адрес найден, но соединение не проходит / обрывается — блокировка, файрвол, антивирус, прокси.
Скорость — 4 МБ с общедоступной проверки Cloudflare (speed.cloudflare.com): обычный GET без личных данных, не дольше 6 секунд.
"""
from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .net import USER_AGENT

RELAY = ("13.143.207.131", 7000)
SLOW_MS = 1500

# (название, адрес, зачем это игроку)
SITES = [
    ("Mojang: версии игры", "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", "без него не скачать игру и Java"),
    ("Mojang: файлы игры", "https://libraries.minecraft.net/", "библиотеки игры"),
    ("Modrinth: каталог модов", "https://api.modrinth.com/v2/", "моды, обновления модов, моды друзей в комнате"),
    ("CurseForge: каталог модов", "https://api.curseforge.com/", "второй каталог модов"),
    ("Ely.by: вход и скины", "https://authserver.ely.by/", "вход через Ely.by"),
    ("Сайт VaultMC", "https://vaultmc.vaultmc.workers.dev/version.json", "обновления лаунчера"),
    ("Сервер VaultMC", "https://vaultmc-skins.vaultmc.workers.dev/api/status", "друзья, комнаты, скины, коды сборок"),
]


@dataclass
class Result:
    name: str
    status: str          # ok | slow | fail
    ms: int | None
    detail: str
    hint: str = ""
    why: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "ms": self.ms, "detail": self.detail, "hint": self.hint, "why": self.why}


def _doh_exists(host: str) -> bool:
    """Есть ли адрес по версии Cloudflare (DNS поверх HTTPS)? Если да, а система его не находит — виноват DNS провайдера."""
    import json
    url = f"https://cloudflare-dns.com/dns-query?name={urllib.parse.quote(host)}&type=A"
    req = urllib.request.Request(url, headers={"Accept": "application/dns-json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return bool(json.loads(r.read(20000)).get("Answer"))
    except (OSError, ValueError):
        return False


def _http(url: str, timeout: float = 8.0) -> tuple[int | None, int, str, bool]:
    """(HTTP-код | None, миллисекунды, ошибка, это ошибка DNS)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)
            return r.status, int((time.perf_counter() - start) * 1000), "", False
    except urllib.error.HTTPError as exc:        # сервер ответил — связь есть
        exc.close()
        return exc.code, int((time.perf_counter() - start) * 1000), "", False
    except urllib.error.URLError as exc:
        reason = exc.reason
        dns = isinstance(reason, socket.gaierror)
        text = "не находится адрес (DNS)" if dns else "таймаут" if isinstance(reason, (socket.timeout, TimeoutError)) \
            else "ошибка сертификата (антивирус или прокси подменяет защищённое соединение?)" if isinstance(reason, ssl.SSLError) \
            else str(reason)[:120]
        return None, int((time.perf_counter() - start) * 1000), text, dns
    except (OSError, ValueError) as exc:
        return None, int((time.perf_counter() - start) * 1000), str(exc)[:120], False


def _relay(timeout: float = 6.0) -> tuple[bool, int, str]:
    start = time.perf_counter()
    try:
        with socket.create_connection(RELAY, timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"PING\n")
            ok = s.recv(16).strip() == b"PONG"
        return ok, int((time.perf_counter() - start) * 1000), "" if ok else "ответил странно"
    except OSError as exc:
        return False, int((time.perf_counter() - start) * 1000), "таймаут" if isinstance(exc, (socket.timeout, TimeoutError)) else str(exc)[:100]


def _speed(seconds: float = 6.0) -> tuple[float, str]:
    """Мбит/с по 4 МБ с Cloudflare; 0 и причина — не измерилось."""
    req = urllib.request.Request("https://speed.cloudflare.com/__down?bytes=4000000", headers={"User-Agent": USER_AGENT})
    start, got = time.perf_counter(), 0
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            while time.perf_counter() - start < seconds:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
    except OSError as exc:
        return 0.0, str(exc)[:80]
    took = max(time.perf_counter() - start, 0.001)
    return (got * 8 / 1e6) / took, "" if got else "пустой ответ"


def _site(name, url, why, http, doh) -> Result:
    status, ms, err, dns = http(url)
    host = urllib.parse.urlparse(url).hostname or ""
    if status is not None:
        slow = ms >= SLOW_MS
        return Result(name, "slow" if slow else "ok", ms, f"отвечает за {ms} мс" + (" — медленно" if slow else ""),
                      "Медленный ответ: возможно, перегружена сеть или далеко до сервера." if slow else "", why)
    if dns and doh(host):
        return Result(name, "fail", None, "адрес не находится у вашего провайдера",
                      f"DNS вашего провайдера не находит {host}, хотя адрес существует. Включите DNS 1.1.1.1 "
                      "(Параметры Windows → Сеть → свойства подключения → DNS) или любой VPN.", why)
    hint = {"Ely.by: вход и скины": "Ely.by часто блокируют провайдеры. Включите VPN или DNS 1.1.1.1; играть можно и оффлайн-аккаунтом.",
            "Сервер VaultMC": "Без сервера VaultMC не работают друзья, комнаты, скины и коды сборок. Попробуйте VPN или DNS 1.1.1.1.",
            "Сайт VaultMC": "Лаунчер не сможет проверить обновления. Скачать новую версию можно с сайта в браузере.",
            "Mojang: версии игры": "Без Mojang нельзя скачать игру. Проверьте интернет, антивирус, прокси; попробуйте VPN."
            }.get(name, "Связь с этим сайтом не проходит: проверьте файрвол, антивирус, прокси или включите VPN.")
    return Result(name, "fail", None, err or "не отвечает", hint, why)


def run(http=_http, doh=_doh_exists, relay=_relay, speed=_speed, sites=None) -> dict:
    """Проверяет всё параллельно. Возвращает {"items": [...], "summary": str, "level": ok|warn|bad, "text": str}."""
    sites = sites or SITES
    items: list[Result] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_site, n, u, w, http, doh) for n, u, w in sites]
        relay_fut = pool.submit(relay)
        speed_fut = pool.submit(speed)
        items = [f.result() for f in futs]
        ok, ms, err = relay_fut.result()
        mbit, speed_err = speed_fut.result()
    if ok:
        items.append(Result("Ретранслятор VaultMC", "slow" if ms >= SLOW_MS else "ok", ms, f"отвечает за {ms} мс" + (" — медленно" if ms >= SLOW_MS else ""),
                            "Игра с друзьями через ретранслятор будет с заметной задержкой." if ms >= SLOW_MS else "", "комнаты для игры с друзьями"))
    else:
        items.append(Result("Ретранслятор VaultMC", "fail", None, err or "не отвечает",
                            "Комнаты через ретранслятор не заработают. Друзья смогут зайти напрямую или через e4mc; попробуйте VPN.",
                            "комнаты для игры с друзьями"))
    if mbit:
        items.append(Result("Скорость скачивания", "slow" if mbit < 8 else "ok", None, f"около {mbit:.0f} Мбит/с",
                            "Скачивание игры и модов будет долгим: сборка в 500 МБ — около "
                            f"{500 * 8 / max(mbit, 0.1) / 60:.0f} мин." if mbit < 8 else "", "скорость загрузки игры и модов"))
    else:
        items.append(Result("Скорость скачивания", "slow", None, f"не измерилась ({speed_err})", "", "скорость загрузки игры и модов"))
    failed = [i for i in items if i.status == "fail"]
    slow = [i for i in items if i.status == "slow"]
    net_sites = [i for i in items if i.name != "Скорость скачивания"]
    if net_sites and all(i.status == "fail" for i in net_sites):
        level, summary = "bad", "Интернета нет или всё блокируется: проверьте подключение, прокси, файрвол и антивирус."
    elif failed:
        level, summary = "bad", f"Не открывается: {', '.join(i.name for i in failed)}."
    elif slow:
        level, summary = "warn", f"Всё открывается, но медленно: {', '.join(i.name for i in slow)}."
    else:
        level, summary = "ok", "Всё в порядке: нужные сайты открываются быстро."
    return {"items": [i.as_dict() for i in items], "summary": summary, "level": level, "text": format_text(items, summary)}


def format_text(items: list[Result], summary: str) -> str:
    mark = {"ok": "OK  ", "slow": "МЕДЛ", "fail": "НЕТ "}
    lines = [summary, ""]
    for i in items:
        lines.append(f"{mark[i.status]} {i.name}: {i.detail}")
        if i.hint:
            lines.append(f"       → {i.hint}")
    return "\n".join(lines)
