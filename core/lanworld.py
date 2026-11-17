"""
Мод LAN World Plug-n-Play (mcwifipnp) хранит настройки «Открыть для сети» у каждого мира в saves/<мир>/mcwifipnp.json.
Чтобы в комнату заходили друзья без лицензии, там нужно выключить «Online Mode». Раньше игрок искал эту кнопку сам —
теперь лаунчер выключает её во всех мирах сборки: перед запуском и, пока игра идёт, в только что созданных мирах.
"""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path


def has_mod(game_dir: Path) -> bool:
    mods = Path(game_dir) / "mods"
    try:
        return any(f.name.lower().startswith("mcwifipnp") and f.name.lower().endswith(".jar") for f in mods.iterdir())
    except OSError:
        return False


def _defaults() -> dict:
    # те же поля, что мод записывает сам (mcwifipnp 1.7.x); порт — свободный из высокого диапазона
    return {"port": random.randint(49152, 65000), "maxPlayers": 8, "GameMode": "survival", "motd": "LAN World",
            "AllPlayersCheats": False, "Whitelist": False, "UseUPnP": True, "AllowCommands": False,
            "OnlineMode": False, "EnableUUIDFixer": False, "ForceOfflinePlayers": [], "PvP": True,
            "CopyToClipboard": True}


def disable_online_mode(game_dir: Path) -> int:
    """Выключает Online Mode во всех мирах сборки. Возвращает, сколько миров поправлено."""
    saves = Path(game_dir) / "saves"
    if not saves.is_dir():
        return 0
    changed = 0
    for world in saves.iterdir():
        if not (world / "level.dat").is_file():
            continue
        cfg = world / "mcwifipnp.json"
        try:
            data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else _defaults()
            if not isinstance(data, dict):
                continue
            if cfg.is_file() and data.get("OnlineMode") is False:
                continue
            data["OnlineMode"] = False
            cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            changed += 1
        except (OSError, ValueError):
            continue
    return changed


E4MC_DEFAULT = """# Whether to hide the domain on chat and only allow copying
hideDomainInChat = false
# Whether to use the broker to get the best relay based on location or use a hard-coded relay.
useBroker = true
brokerUrl = "https://broker.e4mc.link/getBestRelay"
relayHost = "test.e4mc.link"
relayPort = 25575
# Allows use of certain dedicated server commands such as /ban and /whitelist
restoreDedicatedCommands = true
# Whether to use whitelists on LAN worlds
useWhiteList = false
# Whether to enable sharing LAN worlds with e4mc
hostEnabled = true
"""


def set_e4mc_host(game_dir: Path, enabled: bool) -> bool:
    """Мод e4mc при «Открыть для сети» сам выдаёт адрес в интернете. Лаунчер включает это, только когда игрок
    сам нажал «Открыть мир для друзей»: при обычной игре по сети (Radmin, одна Wi-Fi) никакой комнаты не создаётся.
    Возвращает True, если e4mc в сборке есть."""
    mods = Path(game_dir) / "mods"
    try:
        if not any(f.name.lower().startswith("e4mc") and f.name.lower().endswith(".jar") for f in mods.iterdir()):
            return False
    except OSError:
        return False
    cfg = Path(game_dir) / "config" / "e4mc" / "e4mc.toml"
    want = f"hostEnabled = {'true' if enabled else 'false'}"
    try:
        text = cfg.read_text(encoding="utf-8") if cfg.is_file() else E4MC_DEFAULT
        import re
        # заменяем строку целиком (в том числе с комментарием в конце); [ \t] вместо \s — чтобы не съедать соседние пустые строки.
        # Раньше «hostEnabled = true # …» не находилось, и ниже в файл дописывалась вторая такая же строка (повтор ключа = ошибка TOML)
        new = re.sub(r"^[ \t]*hostEnabled[ \t]*=[^\n]*$", want, text, flags=re.M)
        if new == text and want not in text:
            new = text.rstrip("\n") + "\n" + want + "\n"
        if new != text or not cfg.is_file():
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(new, encoding="utf-8")
    except OSError:
        pass
    return True


def watch(game_dir: Path, alive, log=print, every: float = 3.0) -> None:
    """Пока игра идёт (alive() → True), выключает Online Mode в новых мирах. Запускается в отдельном потоке."""
    def loop():
        stop = threading.Event()
        while alive():
            n = disable_online_mode(game_dir)
            if n:
                log(f"[Комната] Онлайн-режим выключен в новых мирах ({n}) — друзья без лицензии смогут зайти")
            stop.wait(every)
    threading.Thread(target=loop, daemon=True).start()
