"""Провайдер не находит адрес (например, *.e4mc.link): ищем его сами через DNS-over-HTTPS и пускаем игру через локальный мост.

Игра всё равно сначала спрашивает системный DNS, поэтому подсказать ей адрес нельзя. Вместо этого лаунчер сам
находит IP через Cloudflare/Google по HTTPS (по IP-адресам DNS, их провайдер не подменяет), поднимает на 127.0.0.1
мост и отдаёт игре адрес моста. Мост подменяет в первом пакете Minecraft («рукопожатии») имя сервера на настоящее:
туннель e4mc по этому имени понимает, к какой комнате вести. Дальше байты идут как есть.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import threading
import urllib.request
from urllib.parse import quote

# DNS-over-HTTPS по IP-адресам: имени сервера искать не нужно, значит системный DNS не требуется
DOH = (
    ("https://1.1.1.1/dns-query?name={n}&type=A", {"Accept": "application/dns-json"}),
    ("https://8.8.8.8/resolve?name={n}&type=A", {"Accept": "application/dns-json"}),
    ("https://1.0.0.1/dns-query?name={n}&type=A", {"Accept": "application/dns-json"}),
)


def split_host_port(address: str, default_port: int = 25565) -> tuple[str, int]:
    address = address.strip()
    if address.count(":") == 1:
        host, _, port = address.rpartition(":")
        if port.isdigit():
            return host, int(port)
    return address, default_port


def needs_lookup(host: str) -> bool:
    """Имя из букв и точек. Для IP-адреса, localhost и имён без точки поиск не нужен."""
    if not host or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def resolve_system(host: str, timeout: float = 5.0) -> bool:
    """Находит ли адрес системный DNS (с ограничением по времени: у провайдера с проблемой getaddrinfo висит долго)."""
    box: list[bool] = []

    def run() -> None:
        try:
            socket.getaddrinfo(host, None)
            box.append(True)
        except OSError:
            box.append(False)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return bool(box and box[0])


def resolve_doh(host: str, timeout: float = 6.0) -> str:
    """IPv4-адрес имени через DNS-over-HTTPS; пустая строка — не нашли."""
    ctx = ssl.create_default_context()
    for url, headers in DOH:
        try:
            req = urllib.request.Request(url.format(n=quote(host, safe="")), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            for ans in data.get("Answer") or []:
                if ans.get("type") == 1 and ans.get("data"):
                    ipaddress.ip_address(ans["data"])
                    return str(ans["data"])
        except Exception:  # noqa: BLE001  не вышло с этим DNS — пробуем следующий
            continue
    return ""


# ---------------------------------------------------------------- рукопожатие Minecraft
def _read_varint(buf: bytes, pos: int = 0) -> tuple[int, int] | None:
    """(значение, позиция после) или None, если байтов ещё не хватает."""
    value = shift = 0
    while True:
        if pos >= len(buf):
            return None
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7
        if shift > 35:
            raise ValueError("слишком длинное число")


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def rewrite_handshake(buf: bytes, host: str, port: int) -> tuple[bytes, int] | None:
    """Подменяет имя сервера и порт в первом пакете. Возвращает (новый пакет, сколько байт исходного пакета заняло)
    или None, если пакет пришёл не целиком."""
    got = _read_varint(buf, 0)
    if got is None:
        return None
    length, pos = got
    if len(buf) < pos + length:
        return None
    body = buf[pos:pos + length]
    used = pos + length
    pid = _read_varint(body, 0)
    if pid is None or pid[0] != 0:
        raise ValueError("первый пакет — не рукопожатие")
    proto = _read_varint(body, pid[1])
    hlen = _read_varint(body, proto[1]) if proto else None
    if proto is None or hlen is None:
        raise ValueError("битое рукопожатие")
    start = hlen[1]
    old = body[start:start + hlen[0]]
    tail = body[start + hlen[0] + 2:]          # после порта: «следующее состояние»
    suffix = old[old.index(b"\0"):] if b"\0" in old else b""    # у Forge после имени идёт «\0FML…\0» — сохраняем
    new_host = host.encode("utf-8") + suffix
    new_body = body[:proto[1]] + _varint(len(new_host)) + new_host + port.to_bytes(2, "big") + tail
    return _varint(len(new_body)) + new_body, used


class Bridge:
    """Локальный мост 127.0.0.1:порт → ip:порт с подменой имени в рукопожатии."""

    def __init__(self, name: str, ip: str, port: int = 25565):
        self.name, self.ip, self.port = name, ip, port
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.local_port = self.srv.getsockname()[1]
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self.alive = False
        try:
            self.srv.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while self.alive:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        up = None
        try:
            client.settimeout(15)
            buf = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    return
                buf += chunk
                done = rewrite_handshake(buf, self.name, self.port)
                if done:
                    break
            packet, used = done
            up = socket.create_connection((self.ip, self.port), timeout=10)
            up.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            up.sendall(packet + buf[used:])
            client.settimeout(None)
            up.settimeout(None)
            t = threading.Thread(target=self._pipe, args=(up, client), daemon=True)
            t.start()
            self._pipe(client, up)
            t.join(1)
        except (OSError, ValueError):
            pass
        finally:
            for s in (client, up):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def rescue(address: str, log=lambda m: None) -> tuple[str, Bridge | None]:
    """Адрес, с которым запускать игру. Если системный DNS адрес не находит, а DNS-over-HTTPS находит, —
    поднимаем мост и возвращаем «127.0.0.1:порт моста» (и сам мост, чтобы закрыть его потом)."""
    host, port = split_host_port(address)
    if not needs_lookup(host) or resolve_system(host):
        return address, None
    ip = resolve_doh(host)
    if not ip:
        log(f"[DNS] Адрес {host} не находит ни ваш провайдер, ни Cloudflare — комната закрыта или адрес неверный")
        return address, None
    bridge = Bridge(host, ip, port)
    log(f"[DNS] Ваш провайдер не находит {host}. Нашли через Cloudflare ({ip}) и подключаемся через локальный мост")
    return f"127.0.0.1:{bridge.local_port}", bridge
