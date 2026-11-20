"""Включён ли VPN: через какой адаптер Windows идёт трафик в интернет.

Смотрим маршрут до 1.1.1.1 (Find-NetRoute — тот же выбор, что делает сама Windows) и описание адаптера.
VPN, которые не забирают весь трафик (Radmin VPN, Hamachi, ZeroTier — для игры по «локальной сети»),
маршрут в интернет не меняют, поэтому сюда не попадают — и это правильно: пинг они не портят.
Нужно для предупреждения перед «Открыть мир для друзей»: через VPN друзья подключаются хуже и пинг выше.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

CREATE_NO_WINDOW = 0x08000000
CACHE_SECONDS = 60

# Подстроки в имени или описании адаптера (без учёта регистра).
VPN_MARKERS = (
    "vpn", "wireguard", "wintun", "openvpn", "tap-windows", "tap-", "tun", "nordlynx", "proton", "mullvad",
    "surfshark", "expressvpn", "windscribe", "hotspot shield", "cloudflare warp", "warp", "outline", "amnezia",
    "hiddify", "sing-box", "v2ray", "xray", "clash", "anyconnect", "globalprotect", "pangp", "fortinet", "forticlient",
    "wan miniport", "ppp", "ikev2", "l2tp", "sstp", "pptp", "zscaler", "psiphon", "lantern", "happ",
)
# Адаптеры для игры «по локальной сети»: если вдруг через них идёт маршрут — не пугаем.
LAN_TOOLS = ("radmin", "hamachi", "zerotier", "tailscale")

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$r = Find-NetRoute -RemoteIPAddress 1.1.1.1 | Where-Object { $_.InterfaceIndex } | Select-Object -First 1
if (-not $r) { '{}'; exit }
$a = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -IncludeHidden -ErrorAction SilentlyContinue
@{ alias = [string]$r.InterfaceAlias; desc = [string]$a.InterfaceDescription; name = [string]$a.Name } | ConvertTo-Json -Compress
"""

_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def classify(alias: str, desc: str) -> bool:
    """По имени и описанию адаптера: VPN ли это (чистая функция — для тестов)."""
    text = f"{alias} {desc}".lower()
    if any(tool in text for tool in LAN_TOOLS):
        return False
    return any(marker in text for marker in VPN_MARKERS)


def _probe() -> dict:
    if sys.platform != "win32":
        return {"vpn": False, "name": ""}
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", SCRIPT],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                           creationflags=CREATE_NO_WINDOW)
        data = json.loads((p.stdout or "").strip() or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"vpn": False, "name": "", "unknown": True}
    alias, desc = str(data.get("alias") or ""), str(data.get("desc") or "")
    is_vpn = classify(alias, desc)
    return {"vpn": is_vpn, "name": (alias or desc) if is_vpn else ""}


def check(force: bool = False) -> dict:
    """{vpn, name}. Результат держится минуту: PowerShell запускается около секунды."""
    global _cache
    with _lock:
        if not force and _cache and time.time() - _cache[0] < CACHE_SECONDS:
            return dict(_cache[1])
    res = _probe()
    with _lock:
        _cache = (time.time(), res)
    return dict(res)
