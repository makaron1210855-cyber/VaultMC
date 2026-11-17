"""
Мост между интерфейсом (web/) и логикой (core/).

Каждый публичный метод класса Api доступен из JS:  await api.имя_метода(аргументы)
pywebview выполняет вызовы в отдельных потоках, поэтому методы могут работать с сетью.
Исключение в методе = ошибка в JS (показывается уведомлением).
Долгие операции (установка, игра) идут через GameRunner и сообщают о себе событиями (events.py).
"""

from __future__ import annotations

import base64
import json
import re
from collections import deque
import os
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path

from core import elyby, keys, microsoft, updater
from core.accounts import KIND_LABELS, Account, AccountStore, AuthError, password_strength
from core.authlib import ensure_authlib_injector, javaagent_arg
from core.dialogs import native_dialog
from core.packformats import EXPORT_FORMATS, export_any, import_any
from core.config import RAM_MAX, RAM_MIN, ConfigManager, LauncherConfig
from core.friends import FriendStore, make_friend_code, parse_friend_code
from core.gameopts import PRESET_LABELS, apply_preset
from core.instances import PACK_EXT, Instance, InstanceManager
from core.launch import AuthInfo, GameRunner, LaunchTarget
from core.modpacks import install_mrpack
from core.mods import ModManager
from core.modsources import (OPTIMIZATION_FORGE, OPTIMIZATION_PACK, SORTS, install_optimization_pack,
                             optimization_candidates, TUNNEL_MODS, install_tunnel, DistributionBlocked, ModrinthSource, ModIndex, install_pack,
                             sync_index,
                             CF_GAME_MINECRAFT, CURSEFORGE_API, make_source)
from core.net import HttpError, download, get_json
from core.utils import (APP_NAME, APP_VERSION, LAUNCHER_HOME, create_desktop_shortcut, find_java_installs,
                        format_duration, java_major_version, open_in_file_manager, timing_log, total_ram_gb)
from core.versions import TYPE_LABELS, VersionRepository, display_name, fabric_id

from .events import EventBus


def _log(line: str) -> None:
    """Строка в ~/.vaultmc/launcher.log (для разбора ошибок)."""
    try:
        with open(LAUNCHER_HOME / "launcher.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass

OPEN_PACK_FILE = LAUNCHER_HOME / "open_pack.txt"   # путь к сборке, открытой двойным щелчком (см. main.py)
SKINS_DIR = LAUNCHER_HOME / "skins"
CAPES_DIR = LAUNCHER_HOME / "capes"
SKIN_CACHE = LAUNCHER_HOME / "cache" / "skins"


def _account_dict(acc: Account) -> dict:
    return {"id": acc.id, "kind": acc.kind, "kindLabel": KIND_LABELS.get(acc.kind, acc.kind),
            "username": acc.username, "playTime": format_duration(acc.play_seconds), "launches": acc.launches,
            "lastInstance": acc.last_instance}


def _major(version: str) -> int:
    """'3.0.1' → 3 (масштабное обновление — когда меняется первая цифра)."""
    try:
        return int(version.strip().lstrip("v").split(".")[0])
    except ValueError:
        return 0


LOADER_LABELS = {"vanilla": "Vanilla", "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge"}


def _instance_dict(inst: Instance, data_dir: Path) -> dict:
    mods = inst.mods_dir
    count = sum(1 for p in mods.iterdir() if p.suffix == ".jar") if mods.is_dir() else 0
    return {**asdict(inst), "loaderLabel": LOADER_LABELS.get(inst.loader, "Vanilla"),
            "playTime": format_duration(inst.play_seconds), "modsCount": count,
            "installed": (data_dir / "versions" / inst.version_id / f"{inst.version_id}.json").exists(),
            "path": str(inst.game_dir), "icon": _icon_data(inst)}


def _minimize_browser_window(minimize: bool) -> None:
    """Свернуть/развернуть окно лаунчера в Edge: свёрнутое окно не рисуется и не отнимает FPS у игры."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if title.value == APP_NAME and cls.value.startswith("Chrome_WidgetWin"):
                found.append(hwnd)
            return True
        user32.EnumWindows(callback, 0)
        for hwnd in found:
            user32.ShowWindow(hwnd, 6 if minimize else 9)  # SW_MINIMIZE / SW_RESTORE
    except Exception as exc:  # noqa: BLE001
        _log(f"свернуть окно Edge не удалось: {exc}")


_ICON_CACHE: dict[str, tuple[float, str]] = {}


def _icon_data(inst: Instance) -> str:
    """Своя картинка сборки (icon.png в папке сборки) как data:URL; пусто — рисуем пиксельную иконку."""
    path = inst.root / "icon.png"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _ICON_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    data = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    _ICON_CACHE[str(path)] = (mtime, data)
    return data


GAME_LOCK_MSG = "Во время игры аккаунт сменить нельзя — сначала закройте игру"


def _norm_title(text: str) -> str:
    """«Cloth Config API (Fabric/Forge/NeoForge)» и «Cloth Config API» → одинаково: до скобок, только буквы и цифры."""
    base = re.split(r"[\(\[]", str(text or ""), 1)[0]
    return re.sub(r"[^a-z0-9а-яё]", "", base.lower())


def _is_installed(hit, index, have: dict):
    """(установлен, включён) для мода из каталога. Совпадение — по записи лаунчера (тот же сайт),
    по файлу (хэш, любой сайт) или по названию/slug. Файлы на Modrinth и CurseForge часто разные."""
    src = hit.source
    if index and index.has(src, hit.id):
        return True, bool(index.is_enabled(src, hit.id))
    if str(hit.id) in have.get(src, set()):
        return True, str(hit.id) in have.get("enabled_" + src, set())
    if index:
        title = _norm_title(hit.title)
        slug = str(getattr(hit, "slug", "") or "").lower()
        for key, entry in index.data.items():
            if not isinstance(entry, dict) or entry.get("dir", "mods") != "mods":
                continue
            source, _, pid = key.partition(":")
            if not index.has(source, pid):
                continue
            if (slug and entry.get("slug", "").lower() == slug) or (title and _norm_title(entry.get("title")) == title):
                return True, bool(index.is_enabled(source, pid))
    return False, False


def _host_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    """Открывается ли соединение с сайтом (две попытки). Нужно, чтобы заранее сказать про проблемы с сетью."""
    import socket
    for _ in range(2):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            time.sleep(0.5)
    return False


class Api:
    def __init__(self, bus: EventBus):
        self._bus = bus
        self._window = None
        self._maximized = False
        self._cfg_manager = ConfigManager()
        self._cfg: LauncherConfig = self._cfg_manager.config
        self._install_ely_prover()
        threading.Thread(target=self._presence_loop, daemon=True).start()
        if not self._cfg.tunnel_migrated:   # e4all заменён на e4mc + LAN World Plug-n-Play (один раз)
            if self._cfg.tunnel_mod in ("e4all", "", None):
                self._cfg.tunnel_mod = "e4mc+lan"
            self._cfg.tunnel_migrated = True
        self._accounts = AccountStore()
        self._instances = InstanceManager()
        self._friends = FriendStore()
        self._repo = VersionRepository(Path(self._cfg.data_dir))
        self._runner = GameRunner(self._cfg, self._repo, self._on_runner_event)
        self._logs: deque[str] = deque(maxlen=5000)   # старые строки уходят сами, без сдвига всего списка
        self._ms_cancel = threading.Event()
        self._room_address = ""
        from core.p2pbridge import P2PBridge
        self._p2p = P2PBridge(lambda m: self._on_runner_event("log", m))
        self._p2p_hosting = False
        self._lan_open = False            # мир открыт для сети (игра написала «Started serving on ...»)
        self._lan_port = 0
        # Комната (адрес e4mc, прямое подключение, код) — только если игрок сам нажал «Открыть мир для друзей».
        # Обычное «Открыть для сети» (Radmin, одна Wi-Fi) лаунчер не трогает.
        self._room_pending = ("", 0.0)    # (сборка, когда нажали) — её сейчас запустят как комнату
        self._room_mode = False
        self._room_code = ""
        self._room_mods: list = []        # моды хозяина, которые нужны и гостю (core/roommods.py)
        self._room_relay = None           # {"port", "token"} — комната на ретрансляторе VaultMC с ключом
        self._relay_guest = None          # гость: мост 127.0.0.1 → ретранслятор (core/relay.py RelayGuest)
        self._guest_server = ""           # сборка, в чей список серверов добавлен мир друга
        self._running_instance = ""
        self._running_account = ""
        self._game_lines: deque[str] = deque(maxlen=4000)   # вывод текущего запуска — для разбора вылета
        threading.Thread(target=self._refresh_versions_bg, daemon=True).start()
        threading.Thread(target=self._repair_shortcut, daemon=True).start()
        threading.Thread(target=self._open_pack_loop, daemon=True).start()
        threading.Thread(target=self._startup_checks, daemon=True).start()

    # ================================================================= служебное
    def _attach_window(self, window) -> None:
        self._window = window
        self._maximized = False
        try:  # следим за состоянием окна: его меняют и кнопка, и Win+↑/↓, и двойной щелчок
            window.events.maximized += lambda *_: self._set_maximized(True)
            window.events.restored += lambda *_: self._set_maximized(False)
            window.events.minimized += lambda *_: None
        except Exception:  # noqa: BLE001 — старый pywebview без этих событий
            pass

    def _set_maximized(self, value: bool) -> None:
        self._maximized = value
        self._bus.emit("win_state", "maximized" if value else "normal")

    def _emit(self, event: str, payload=None) -> None:
        self._bus.emit(event, payload)

    def _on_runner_event(self, event: str, payload) -> None:
        if event == "log":
            self._logs.append(str(payload))
            if self._runner.state == "running":
                self._game_lines.append(str(payload))
        elif event == "room_address":
            if not self._room_mode:      # мир открыли не как комнату — адрес не показываем и друзьям не шлём
                return
            relay = getattr(self, "_relay", None)
            if relay and relay.public_port and not relay.secure:
                return                   # комната без ключа: её адрес на ретрансляторе главнее туннеля e4mc
            # комната с ключом: адрес e4mc — запасной путь (и для друзей без VaultMC)
            self._room_address = str(payload)
            self._room_update()
            self._presence_soon()
        elif event == "lan_port":
            self._lan_open = True
            self._lan_port = int(payload)
            self._emit("lan_open", {"port": int(payload), "room": self._room_mode})
            if self._room_mode:
                self._room_start(int(payload))
            return
        elif event == "game_exit":
            try:
                self._after_game(payload)
            except Exception as exc:  # noqa: BLE001  окно о вылете должно показаться в любом случае
                _log(f"after_game: {exc}")
        elif event == "state":
            self._bus.set_gaming(payload in ("running", "preparing"))
            if payload == "running":
                self._game_lines.clear()
                self._watch_lan_world()
            if payload == "idle":
                self._lan_open = False
                self._lan_port = 0
                self._room_stop()
                self.relay_guest_stop()
                self._guest_server_remove()
            self._toggle_window(payload)
            if payload == "idle" and self._p2p_hosting:
                self._p2p_hosting = False
                self._p2p.lan_port = 0
                self._emit("p2p_stop", None)
            if payload in ("running", "idle"):
                self._presence_soon()
        self._emit(event, payload)

    # ---------------------------------------------------------------- комната по кнопке
    def _room_info(self) -> dict:
        inst = self._instances.get(self._running_instance)
        return {"instance": inst.name if inst else "", "version": inst.game_version if inst else "",
                "loader": inst.loader if inst else "", "room": self._room_address, "p2p": self._p2p_hosting,
                "mods": self._room_mods, "relay": self._room_relay}

    def _room_start(self, port: int) -> None:
        """Мир открыт как комната: открываем её на ретрансляторе VaultMC и берём на сервере код комнаты."""
        self._relay_start(port)

        def job():
            from core import skinserver
            acc = self._accounts.active
            if not acc or acc.kind not in ("offline", "elyby") or not skinserver.server_url():
                return
            try:   # моды, без которых гостя выкинет из мира: его лаунчер предложит поставить только их
                from core import roommods
                inst = self._instances.get(self._running_instance)
                self._room_mods = roommods.needed_mods(inst.mods_dir, inst.loader, lambda m: self._on_runner_event("log", m)) if inst else []
            except Exception as exc:  # noqa: BLE001
                self._room_mods = []
                _log(f"room mods: {exc}")
            try:
                code = skinserver.room(acc.username, "open", info=self._room_info()).get("code", "")
            except Exception as exc:  # noqa: BLE001
                _log(f"room open: {exc}")
                self._emit("room_problem", f"Код комнаты не получен: {exc}. Друзья из списка всё равно могут зайти.")
                return
            if not code:
                return
            if not self._room_mode or self._runner.state != "running":   # игра закрылась, пока ждали код — закрываем
                try:
                    skinserver.room(acc.username, "close", code)
                except Exception:  # noqa: BLE001
                    pass
                return
            self._room_code = code
            self._emit("room_code", code)
            self._presence_soon()
        threading.Thread(target=job, daemon=True).start()

    def _relay_start(self, port: int) -> None:
        from core.relay import RelayHost
        self._relay_stop()

        def on_port(pub: int) -> None:
            from core.relay import RELAY_HOST
            if relay.secure:   # с ключом: гостям — порт и ключ (заходят через свой лаунчер), адрес не публикуем
                self._room_relay = {"port": pub, "token": relay.token}
            else:              # ретранслятор старой версии — как раньше, общий адрес
                self._room_relay = None
                self._room_address = f"{RELAY_HOST}:{pub}"
            self._room_update()
            self._presence_soon()
            self._emit("room_address", self._room_address)
        relay = RelayHost(port, lambda m: self._on_runner_event("log", m), on_port)
        self._relay = relay
        try:
            relay.start()
        except OSError as exc:
            self._relay = None
            self._on_runner_event("log", f"[Комната] ретранслятор недоступен ({exc}) — комната через e4mc")

    def _relay_stop(self) -> None:
        relay = getattr(self, "_relay", None)
        self._relay = None
        if relay:
            relay.stop()

    def _room_update(self) -> None:
        code, acc = self._room_code, self._accounts.active
        if not code or not acc:
            return

        def job():
            from core import skinserver
            try:
                skinserver.room(acc.username, "update", code, self._room_info())
            except Exception as exc:  # noqa: BLE001
                _log(f"room update: {exc}")
        threading.Thread(target=job, daemon=True).start()

    def _room_stop(self) -> None:
        code, acc = self._room_code, self._accounts.active
        self._relay_stop()
        self._room_mode, self._room_code, self._room_address = False, "", ""
        self._room_relay = None
        if not code or not acc:
            return

        def job():
            from core import skinserver
            try:
                skinserver.room(acc.username, "close", code)
            except Exception as exc:  # noqa: BLE001
                _log(f"room close: {exc}")
        threading.Thread(target=job, daemon=True).start()

    def open_room_now(self) -> dict:
        """Игра уже идёт и мир открыт для сети обычным способом — сделать из него комнату (код + прямое подключение)."""
        if self._runner.state != "running" or not self._lan_open or not self._lan_port:
            return {"error": "Сначала откройте мир для сети: в игре Esc → «Открыть для сети»"}
        if self._room_mode:
            return {"ok": True, "code": self._room_code}
        self._room_mode = True
        self._room_start(self._lan_port)
        self._emit("lan_open", {"port": self._lan_port, "room": True})
        return {"ok": True}

    def room_state(self) -> dict:
        return {"mode": self._room_mode, "code": self._room_code, "address": self._room_address,
                "lanOpen": self._lan_open, "p2p": self._p2p_hosting, "relay": bool(self._room_relay)}

    def room_lookup(self, code: str) -> dict:
        """Гость ввёл код комнаты — к кому и в какую версию заходить."""
        from core import skinserver
        code = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
        if len(code) != 6:
            return {"error": "Код комнаты — 6 букв и цифр, например K7XQ2M"}
        acc = self._accounts.active
        try:
            if acc and acc.kind in ("offline", "elyby"):
                try:    # от имени игрока: заблокировавший вас хозяин для вас «комнату закрыл»
                    data = skinserver.lookup_room(acc.username, code)
                except skinserver.SkinServerError as exc:
                    if exc.status == 404 or exc.code == "gone":
                        raise
                    data = skinserver.get_room(code)    # старый сервер или сбой — как раньше, без имени
            else:
                data = skinserver.get_room(code)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        if acc and data.get("host", "").lower() == acc.username.lower():
            return {"error": "Это код вашей собственной комнаты"}
        return {"ok": True, "code": code, "host": data.get("host", ""), "info": data.get("info") or {}}

    def room_mods_check(self, instance_id: str, mods) -> dict:
        """Гость: каких модов хозяина нет в его сборке и можно ли их туда поставить."""
        from core import roommods
        inst = self._instances.get(instance_id)
        if not inst:
            return {"missing": [], "can": False}
        try:
            missing = roommods.missing_mods(inst.mods_dir, mods)
        except Exception as exc:  # noqa: BLE001
            _log(f"room mods check: {exc}")
            missing = []
        return {"missing": missing, "can": inst.loader in ("fabric", "quilt"), "instance": inst.name}

    def room_mods_install(self, instance_id: str, mods) -> dict:
        """Гость согласился — ставим в его сборку выбранные моды хозяина (событие room_mods_done по окончании)."""
        from core import roommods
        inst = self._inst(instance_id)
        if inst.loader not in ("fabric", "quilt"):
            return {"error": "В эту сборку моды друга не поставить — нужна сборка Fabric"}
        mods = roommods.clean_list(mods)
        started = self._runner.run_task(
            "Ставлю моды друга...",
            lambda: {"instance": inst.id,
                     "result": roommods.install_mods(inst.game_dir, inst.game_version, inst.loader, mods,
                                                     lambda m: self._on_runner_event("log", m))},
            "room_mods_done")
        return {"ok": True} if started else {"error": "Дождитесь окончания текущей операции"}

    # ---------------------------------------------------------------- мир друга в «Сетевой игре» у гостя
    GUEST_SERVER_PREFIX = "VaultMC · "

    def p2p_guest_server(self, host: str, port: int) -> dict:
        """Прямое подключение готово — кладём мир друга в список серверов сборки: если игра вылетит или
        выйдете в меню, зайти снова можно из «Сетевой игры», как с Radmin."""
        from core.worlds import list_servers, save_servers
        inst = self._instances.get(self._cfg.selected_instance)
        if not inst:
            return {}
        try:
            servers = [s for s in list_servers(inst.game_dir) if not s["name"].startswith(self.GUEST_SERVER_PREFIX)]
            save_servers(inst.game_dir, [{"name": f"{self.GUEST_SERVER_PREFIX}мир {host}", "ip": f"127.0.0.1:{int(port)}"}] + servers)
            self._guest_server = inst.id
        except Exception as exc:  # noqa: BLE001
            _log(f"guest server: {exc}")
        return {"ok": True}

    # ---------------------------------------------------------------- гость через ретранслятор VaultMC
    def relay_guest_start(self, host: str, port, token: str) -> dict:
        """Зайти к другу через ретранслятор: проверяем комнату, поднимаем мост на 127.0.0.1 и кладём мир друга
        в «Сетевую игру». Вылетел или вышел в меню — заходите оттуда снова, мост живёт до закрытия игры."""
        from core.relay import RelayError, RelayGuest
        if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")) or not str(port).isdigit():
            return {"error": "Неверные данные комнаты", "reason": "bad"}
        self.relay_guest_stop()
        guest = RelayGuest(int(port), str(token), lambda m: self._on_runner_event("log", m))
        try:
            guest.check()
        except RelayError as exc:
            return {"error": str(exc), "reason": exc.reason}
        local = guest.start()
        self._relay_guest = guest
        self.p2p_guest_server(host, local)
        return {"ok": True, "address": f"127.0.0.1:{local}"}

    def relay_guest_stop(self) -> None:
        guest, self._relay_guest = self._relay_guest, None
        if guest:
            guest.stop()

    def net_status(self) -> dict:
        """Связь гостя через ретранслятор: задержка, сколько данных прошло, последняя ошибка. Пусто — не подключены."""
        guest = self._relay_guest
        return {"mode": "relay", **guest.stats()} if guest else {}

    def _guest_server_remove(self) -> None:
        from core.worlds import list_servers, save_servers
        inst = self._instances.get(self._guest_server) if self._guest_server else None
        self._guest_server = ""
        if not inst:
            return
        try:
            servers = list_servers(inst.game_dir)
            keep = [s for s in servers if not s["name"].startswith(self.GUEST_SERVER_PREFIX)]
            if len(keep) != len(servers):
                save_servers(inst.game_dir, keep)
        except Exception as exc:  # noqa: BLE001
            _log(f"guest server remove: {exc}")

    def _watch_lan_world(self) -> None:
        """В сборке есть LAN World Plug-n-Play — во время игры выключаем «Online Mode» и в новых мирах."""
        from core import lanworld
        inst = self._instances.get(self._running_instance)
        if inst and lanworld.has_mod(inst.game_dir):
            lanworld.watch(inst.game_dir, lambda: self._runner.state == "running",
                           lambda m: self._on_runner_event("log", m))

    def _toggle_window(self, state: str) -> None:
        """Лаунчер во время игры больше не прячется (так проще и стабильнее). После игры на всякий случай
        показываем окно — вдруг его скрыла старая версия лаунчера."""
        if state != "idle":
            return
        if self._window is None:
            _minimize_browser_window(False)
            return
        try:
            self._window.show()
        except Exception:  # noqa: BLE001
            pass

    def _after_game(self, info: dict) -> None:
        inst = self._instances.get(self._running_instance)
        if inst:
            inst.play_seconds += info["seconds"]
            inst.last_played = time.time()
            self._instances.save(inst)
        acc_id = self._running_account or (self._accounts.active.id if self._accounts.active else "")
        if acc_id:
            self._accounts.record_session(acc_id, info["seconds"], inst.name if inst else "")
        self._room_address = ""
        if inst and self._cfg.auto_world_backups:
            self._auto_world_backups(inst)
        if inst and info.get("seconds", 0) >= 300:
            self._lag_hint(inst)
        if info.get("code") not in (0, None) and not info.get("killed"):
            info["instance"] = inst.id if inst else ""
            info["analysis"] = self._analyze_crash(info, inst)

    def _lag_hint(self, inst) -> None:
        """Игра шла 5+ минут — смотрим журналы; если много подвисаний, подсказываем кнопку «Почему тормозит»."""
        def job():
            try:
                res = self.lag_check(inst.id)
            except Exception as exc:  # noqa: BLE001  подсказка — не повод что-то ломать
                _log(f"lag hint: {exc}")
                return
            bad = res["behind"] >= 5 or res["gc"]["over500"] >= 2 or res["gc"]["full"] >= 3
            if bad:
                self._emit("lag_hint", {"instance": inst.id, "text": "В журнале есть подвисания: игра не успевала или долго убирала память. "
                                                                     "Лаунчер может показать причину и что поправить."})
        threading.Thread(target=job, daemon=True).start()

    def _auto_world_backups(self, inst) -> None:
        """После выхода из игры — в фоне копируем миры, в которых играли (по 5 автокопий на мир)."""
        from core import worlds as W
        log = lambda m: self._on_runner_event("log", m)   # noqa: E731

        def job():
            for _ in range(40):          # событие выхода приходит чуть раньше, чем лаунчер отметит «игра закрыта»
                if not self._instance_running(inst.id):
                    break
                time.sleep(0.5)
            try:
                done = W.auto_backup_changed(inst.game_dir, inst.root / "backups",
                                             lambda: not self._instance_running(inst.id), log)
            except Exception as exc:  # noqa: BLE001  копии — не повод мешать игроку
                log(f"[Миры] автокопии: {exc}")
                return
            if done:
                log(f"[Миры] автокопия: {', '.join(done)}")
                self._emit("world_autobackup", {"instance": inst.id, "worlds": done})
        threading.Thread(target=job, daemon=True).start()

    def _analyze_crash(self, info: dict, inst: Instance | None) -> list:
        from core.crash import analyze
        report = ""
        if info.get("crash"):
            try:
                report = Path(info["crash"]).read_text(encoding="utf-8", errors="replace")[:200_000]
            except OSError:
                pass
        try:
            # память берём ту, с которой игра реально запускалась (для больших сборок лаунчер сам даёт 6–8 ГБ,
            # а не общую из настроек — иначе при нехватке памяти предлагалось бы «увеличить» до числа меньше текущего)
            ram = getattr(self, "_last_ram", 0) or (inst.ram_gb if inst and inst.ram_gb else self._cfg.ram_gb)
            return analyze("\n".join(self._game_lines), report, inst.mods_dir if inst else None,
                           ram, info.get("code", 1))
        except Exception as exc:  # noqa: BLE001  разбор не должен ломать лаунчер
            _log(f"crash analyze: {exc}")
            return []

    def crash_fix(self, instance_id: str, action: str, arg="") -> dict:
        """Исправление из окна «Игра вылетела»."""
        inst = self._instances.get(instance_id)
        if action == "java_auto":
            self._cfg.java_auto = True
            self._save()
            return {"ok": "Автоподбор Java включён — нажмите «Играть»"}
        if action == "clear_jvm":
            had = bool((self._cfg.extra_jvm_args or "").strip() or (inst and (inst.jvm_args or "").strip()))
            self._cfg.extra_jvm_args = ""
            if inst:
                inst.jvm_args = ""
                self._instances.save(inst)
            self._save()
            return {"ok": "Свои флаги JVM убраны — нажмите «Играть»" if had else "Своих флагов JVM не было — проблема в другом"}
        if action in ("ram", "ram_down") and inst:
            inst.ram_gb = max(RAM_MIN, min(RAM_MAX, int(arg)))
            self._instances.save(inst)
            return {"ok": f"Сборке выделено {inst.ram_gb} ГБ"}
        if action == "install_dep" and inst and arg:
            dep = str(arg)
            last = ""
            for slug in dict.fromkeys([dep, dep.replace("_", "-"), dep.replace("-", "_")]):
                try:
                    ModrinthSource().install_by_slug(slug, inst.game_version, inst.loader, inst.game_dir)
                    return {"ok": f"Мод «{dep}» установлен"}
                except Exception as exc:  # noqa: BLE001
                    last = str(exc)
            return {"error": f"Не нашёл «{dep}» для {inst.game_version} на Modrinth ({last[:80]}). Найдите его в каталоге.",
                    "find": dep}
        if action == "disable_mod" and inst and arg:
            path = inst.mods_dir / Path(str(arg)).name
            if path.is_file():
                path.rename(path.with_name(path.name + ".disabled"))
                return {"ok": f"Мод {path.name} выключен"}
            return {"error": "Мод не найден — возможно, его уже убрали"}
        if action == "rollback_mod" and inst:
            from core.modupdates import rollback
            try:
                return {"ok": f"Вернул старую версию: {rollback(inst.game_dir, str(arg))}"}
            except (ValueError, OSError) as exc:
                return {"error": str(exc)}
        if action in ("compat_on", "compat_off") and inst:
            inst.compat = action == "compat_on"
            self._instances.save(inst)
            return {"ok": "Режим совместимости включён — нажмите «Играть»" if inst.compat else "Режим совместимости выключен"}
        if action == "disable_mods" and inst and isinstance(arg, list):
            done = 0
            for name in arg:
                path = inst.mods_dir / Path(str(name)).name
                if path.is_file():
                    try:
                        path.rename(path.with_name(path.name + ".disabled"))
                        done += 1
                    except OSError:      # один занятый файл не должен прерывать выключение остальных
                        continue
            return {"ok": f"Выключено модов: {done}"} if done else {"error": "Моды не найдены — возможно, их уже убрали"}
        if action in ("shaders_off", "drop_pack", "reset_visuals") and inst:
            from core import repair
            try:
                if action == "shaders_off":
                    return {"ok": repair.shaders_off(inst.game_dir)}
                if action == "drop_pack":
                    return {"ok": repair.drop_pack(inst.game_dir, str(arg))}
                return {"ok": repair.reset_visuals(inst.game_dir)}
            except OSError as exc:
                return {"error": f"Не удалось изменить файл: {exc}"}
        return {"error": "Это исправление недоступно"}

    # ================================================================= починка сборки
    def repair_scan(self, instance_id: str) -> dict:
        """«Починка сборки»: что может мешать запуску или давать чёрный экран. Ничего не меняет."""
        from core import repair
        inst = self._inst(instance_id)
        if self._runner.state == "running" and self._running_instance == inst.id:
            return {"error": "Сначала закройте игру: пока она открыта, файлы сборки менять нельзя"}
        try:
            items = repair.scan(inst.game_dir, inst.mods_dir, inst.loader, inst.game_version, inst.ram_gb or self._cfg.ram_gb,
                                compat=inst.compat)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Не удалось проверить сборку: {exc}"}
        mods = sum(1 for p in inst.mods_dir.iterdir() if p.suffix.lower() == ".jar") if inst.mods_dir.is_dir() else 0
        return {"items": items, "mods": mods, "loader": inst.loader, "game": inst.game_version}

    def repair_apply(self, instance_id: str, ids: list) -> dict:
        """Применяет исправления выбранных находок (все обратимы). Возвращает, что сделано."""
        from core import repair
        inst = self._inst(instance_id)
        if self._runner.state == "running" and self._running_instance == inst.id:
            return {"error": "Сначала закройте игру"}
        wanted = set(map(str, ids or []))
        self._auto_snapshot(inst, "Перед «Починкой»")
        items = repair.scan(inst.game_dir, inst.mods_dir, inst.loader, inst.game_version, inst.ram_gb or self._cfg.ram_gb,
                            compat=inst.compat)
        done, failed, skipped = [], [], []
        for it in items:
            if it["id"] not in wanted:
                continue
            fix = it.get("fix")
            if not fix or fix["action"] in ("logs", "find_mod"):
                skipped.append(it["title"])
                continue
            r = self.crash_fix(inst.id, fix["action"], fix["arg"])
            (done if r.get("ok") else failed).append(it["title"] + (" — " + r["error"] if r.get("error") else ""))
        return {"done": done, "failed": failed, "skipped": skipped}

    def repair_compare(self, instance_id: str, file_a: str, file_b: str) -> dict:
        """Какой из двух конфликтующих модов лучше выключить (по данным из самих модов)."""
        from core import repair
        return repair.compare(self._inst(instance_id).mods_dir, file_a, file_b)

    def repair_reset_visuals(self, instance_id: str) -> dict:
        """Если ничего не нашли: сбросить шейдеры и ресурспаки (запасной вариант против чёрного экрана)."""
        return self.crash_fix(instance_id, "reset_visuals", "")

    @property
    def _cf_key(self) -> str:
        """Ключ игрока из настроек, иначе — встроенный ключ разработчика (core/keys.py)."""
        user = (self._cfg.curseforge_api_key or "").strip()
        builtin = keys.CURSEFORGE_API_KEY.strip()
        # Старый ключ из настроек (времён Neon) мог оказаться неверным, а поле теперь скрыто.
        # Проверяем его один раз: если CurseForge его не принимает — стираем и берём встроенный.
        if user and user != builtin and not getattr(self, "_cf_user_checked", False):
            self._cf_user_checked = True
            try:
                get_json(f"{CURSEFORGE_API}/games/{CF_GAME_MINECRAFT}", headers={"x-api-key": user})
            except HttpError as exc:
                if exc.status in (401, 403):
                    self._cfg.curseforge_api_key = ""
                    self._save()
                    user = ""
            except Exception:  # noqa: BLE001 — нет сети: оставляем как есть
                pass
        return user or builtin

    def _save(self) -> None:
        self._cfg_manager.save()

    def _refresh_versions_bg(self) -> None:
        online = self._repo.refresh_remote()
        self._emit("versions_loaded", {"online": online, "count": len(self._repo.remote)})

    def _inst(self, instance_id: str) -> Instance:
        inst = self._instances.get(instance_id)
        if inst is None:
            raise ValueError("Сборка не найдена")
        return inst

    def _dialog(self, kind: str, **kw):
        if self._window is None:  # окно браузера (Edge) — используем системные окна Windows
            self._bus.emit("dialog", True)   # интерфейс покажет подсказку «открыто окно выбора»
            try:
                return native_dialog(kind, **kw)
            finally:
                self._bus.emit("dialog", False)
        import webview
        dialog_types = getattr(webview, "FileDialog", None)
        mapping = {"open": "OPEN", "folder": "FOLDER", "save": "SAVE"}
        dtype = getattr(dialog_types, mapping[kind]) if dialog_types else getattr(webview, f"{mapping[kind]}_DIALOG")
        result = self._window.create_file_dialog(dtype, **kw)
        if not result:
            return None
        return result if isinstance(result, str) else (list(result) if kw.get("allow_multiple") else result[0])

    # ================================================================= окно
    def win_minimize(self):
        if self._window:
            self._window.minimize()

    def win_maximize(self):
        """Кнопка □: развернуть, а если окно уже развёрнуто — вернуть прежний размер."""
        if not self._window:
            return
        try:
            if getattr(self, "_maximized", False):
                self._window.restore()
                self._set_maximized(False)
            else:
                self._window.maximize()
                self._set_maximized(True)
        except Exception:  # noqa: BLE001
            pass

    def win_start_resize(self, edge: str) -> bool:
        """Взялись мышью за край окна — дальше окно тянется за курсором, пока кнопка зажата."""
        from core import winresize
        if not self._window or self._maximized:
            return False
        if not self._ensure_hwnd():
            return False
        return winresize.start(self._hwnd, str(edge), 1100, 700)

    def _ensure_hwnd(self):
        from core import winresize
        if not getattr(self, "_hwnd", None):
            self._hwnd = winresize.find_window(self._window.title)
        return self._hwnd

    def win_restore(self):
        if self._window:
            self._window.restore()

    def win_close(self):
        if self._runner.busy and self._runner.state != "running":
            self._runner.cancel()
        self._save()
        if self._window:
            self._window.destroy()

    def open_url(self, url: str):
        if url.startswith(("https://", "http://")):
            webbrowser.open(url)

    # ================================================================= состояние
    def _repair_shortcut(self) -> None:
        """Установщик пишет, просил ли пользователь ярлык. Если Windows не дала его создать — создаём сами."""
        flag = Path(__file__).resolve().parents[2] / "installed-by-setup.txt"
        try:
            if "desktop=1" not in flag.read_text(encoding="utf-8"):
                return
            desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
            onedrive = Path(os.environ.get("OneDrive", "")) / "Desktop" if os.environ.get("OneDrive") else None
            if any((d / f"{APP_NAME}.lnk").exists() for d in (desktop, onedrive) if d):
                return
            create_desktop_shortcut()
        except Exception:  # noqa: BLE001
            pass

    # ---- свой фон главного экрана ----
    def _lobby_bg_path(self) -> Path | None:
        return next((p for p in LAUNCHER_HOME.glob("lobby_bg.*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")), None)

    def lobby_bg(self) -> str:
        path = self._lobby_bg_path()
        if not path:
            return ""
        mime = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/jpeg")
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()

    def pick_lobby_bg(self) -> dict:
        file = self._dialog("open", file_types=("Картинки (*.png;*.jpg;*.jpeg;*.webp;*.gif)",))
        if not file:
            return {}
        src = Path(file)
        if src.stat().st_size > 12 * 1024 * 1024:
            return {"error": "Картинка больше 12 МБ — выберите поменьше"}
        old = self._lobby_bg_path()
        if old:
            old.unlink(missing_ok=True)
        LAUNCHER_HOME.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, LAUNCHER_HOME / f"lobby_bg{src.suffix.lower()}")
        return {"url": self.lobby_bg()}

    def clear_lobby_bg(self) -> None:
        old = self._lobby_bg_path()
        if old:
            old.unlink(missing_ok=True)

    def bootstrap(self) -> dict:
        acc = self._accounts.active
        return {
            "app": APP_NAME, "version": APP_VERSION, "config": asdict(self._cfg),
            "account": _account_dict(acc) if acc else None,
            "accounts": [_account_dict(a) for a in self._accounts.list()],
            "instances": self.list_instances(), "state": self._runner.state,
            "logs": list(self._logs)[-1500:], "ramTotal": total_ram_gb(), "ramMin": RAM_MIN, "ramMax": RAM_MAX,
            "presets": PRESET_LABELS, "optimization": OPTIMIZATION_PACK, "optimizationForge": OPTIMIZATION_FORGE, "tunnels": TUNNEL_MODS,
            "sorts": list(SORTS), "roomAddress": self._room_address,
            # мир открыт для прямых подключений: после перезагрузки страницы окно снова начнёт их принимать
            "p2pHost": self._p2p.url if self._p2p_hosting else "",
            "lanOpen": bool(getattr(self, "_lan_open", False)),
            "roomMode": self._room_mode, "roomCode": self._room_code,
            "builtinCfKey": bool(keys.CURSEFORGE_API_KEY),
            # установщик Setup.exe сам создаёт ярлык — тогда не предлагаем
            "firstRun": not self._cfg.shortcut_offered and not (Path(__file__).resolve().parents[2] / "installed-by-setup.txt").exists(),
            "inApp": self._window is not None,
            "justUpdated": self._just_updated(),
        }

    def _just_updated(self) -> dict | None:
        """Лаунчер только что обновился через автообновление — покажем, что изменилось с прошлой версии."""
        from core.changelog import notes_since
        previous = self._cfg.ui.get("lastVersion")
        if previous != APP_VERSION:
            self._cfg.ui["lastVersion"] = APP_VERSION
            self._save()
        mark = Path(__file__).resolve().parents[2] / "installed-by-setup.txt"
        try:
            info = dict(line.split("=", 1) for line in mark.read_text(encoding="utf-8").split() if "=" in line)
        except OSError:
            return None
        if info.get("update") != "1" or info.get("version") != APP_VERSION \
                or self._cfg.ui.get("updateShown") == APP_VERSION:
            return None
        # версии до 3.0 не запоминали себя: считаем, что пришли из прошлого поколения
        major = previous is None or _major(previous) < _major(APP_VERSION)
        return {"version": APP_VERSION, "notes": notes_since(None if major and previous is None else previous, APP_VERSION),
                "major": major}

    def ack_update(self) -> None:
        self._cfg.ui["updateShown"] = APP_VERSION
        self._save()

    # ================================================================= обновления
    def check_update(self) -> dict:
        """Есть ли новая версия лаунчера на сайте."""
        try:
            info = updater.check()
        except updater.UpdateError as exc:
            return {"error": str(exc), "current": APP_VERSION}
        self._update_info = info
        return {**{k: info[k] for k in ("available", "version", "notes", "current")},
                "major": _major(info.get("version", "")) > _major(APP_VERSION)}

    def open_update_site(self) -> None:
        """Страница загрузки — если автообновление не вышло (например, антивирус заблокировал файл)."""
        info = getattr(self, "_update_info", None) or {}
        url = info.get("url") or updater.UPDATE_MANIFEST
        webbrowser.open(url.rsplit("/", 1)[0] + "/")

    def install_update(self) -> dict:
        """Скачивает установщик новой версии и запускает его (он закроет лаунчер и откроет новую версию)."""
        info = getattr(self, "_update_info", None)
        if not info or not info.get("available"):
            return {"error": "Сначала проверьте обновления"}
        if self._runner.state in ("running", "preparing"):
            return {"error": "Закройте игру — обновление начнётся после неё"}
        if self._runner.state == "installing" or getattr(self._runner, "busy", False):
            return {"error": "Дождитесь окончания загрузки — потом обновитесь"}
        installed = (Path(__file__).resolve().parents[2] / "installed-by-setup.txt").exists()
        if not installed or os.name != "nt":
            # лаунчер запущен не из установщика — просто открываем страницу загрузки
            webbrowser.open(info["url"].rsplit("/", 1)[0] + "/")
            return {"manual": True}
        if getattr(self, "_updating", False):
            return {"started": True}
        self._updating = True

        def job():
            try:
                path = updater.download_installer(
                    info, lambda f: self._bus.emit("update_progress", round(f * 100)))
                _log(f"обновление {APP_VERSION} → {info['version']}: запускаю установщик")
                self._bus.emit("update_installing", info["version"])
                time.sleep(1.2)  # даём интерфейсу показать «Устанавливаю...»
                self._save()
                updater.run_installer(path)
            except Exception as exc:  # noqa: BLE001
                self._updating = False
                self._bus.emit("update_failed", str(exc))
        threading.Thread(target=job, daemon=True).start()
        return {"started": True}

    # ---- откат лаунчера и проверка его файлов ----
    def _startup_checks(self) -> None:
        """При запуске, в фоне: заметить откат на прошлую версию и проверить свои файлы."""
        try:
            skipped = updater.note_rollback()
            if skipped:
                _log(f"лаунчер откатили с {skipped} на {APP_VERSION} — {skipped} больше не предлагаем")
        except Exception as exc:  # noqa: BLE001
            _log(f"rollback note: {exc}")
        try:
            from core import integrity
            self._damaged = integrity.check(Path(__file__).resolve().parents[1])
            if self._damaged:
                _log(f"файлы лаунчера повреждены: {len(self._damaged)}, например {self._damaged[0]}")
        except Exception as exc:  # noqa: BLE001
            _log(f"integrity: {exc}")

    def launcher_health(self) -> dict:
        """Для настроек и подсказки при запуске: прошлая версия рядом (можно вернуться) и повреждённые файлы."""
        inst = Path(__file__).resolve().parents[2]
        prev = ""
        prev_utils = inst / "app.prev" / "core" / "utils.py"
        if prev_utils.is_file() and (inst / "rollback.cmd").is_file():
            m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', prev_utils.read_text(encoding="utf-8", errors="replace"), re.M)
            prev = m.group(1) if m else "?"
        damaged = getattr(self, "_damaged", [])
        return {"current": APP_VERSION, "previous": prev, "damaged": damaged[:20], "damagedCount": len(damaged)}

    def launcher_rollback(self) -> dict:
        """Вернуть прошлую версию лаунчера: rollback.cmd закроет лаунчер, поменяет папки и запустит прошлую версию."""
        inst = Path(__file__).resolve().parents[2]
        script = inst / "rollback.cmd"
        if os.name != "nt" or not script.is_file() or not (inst / "app.prev" / "main.py").is_file():
            return {"error": "Прошлой версии рядом нет — откатиться не к чему"}
        if self._runner.state in ("running", "preparing", "installing") or getattr(self._runner, "busy", False):
            return {"error": "Закройте игру и дождитесь окончания загрузок"}
        self._save()
        try:
            subprocess.Popen(["cmd", "/c", str(script)], cwd=str(inst), creationflags=0x00000010)   # своё окно консоли
        except OSError as exc:
            return {"error": f"Не удалось запустить откат: {exc}"}
        return {"ok": True}

    def repair_launcher(self) -> dict:
        """Файлы лаунчера повреждены — переустановить его подписанным установщиком с сайта."""
        try:
            self._update_info = updater.fetch_signed()
        except updater.UpdateError as exc:
            return {"error": str(exc)}
        return self.install_update()

    def get_logs(self) -> list:
        return list(self._logs)[-3000:]

    def clear_logs(self):
        self._logs.clear()

    def save_ui(self, key: str, value) -> None:
        """Запоминает мелочи интерфейса (вкладка, фильтры, свёрнутая панель) между запусками."""
        self._cfg.ui[key] = value
        self._save()

    def save_window_size(self, width: int, height: int) -> None:
        if width >= 1000 and height >= 650:
            self._cfg.ui["window"] = [int(width), int(height)]
            self._save()

    def save_settings(self, values: dict) -> dict:
        for key, value in values.items():
            if key in LauncherConfig.__dataclass_fields__ and key != "ui":
                current = getattr(self._cfg, key)
                if isinstance(current, bool):
                    value = bool(value)
                elif isinstance(current, int):
                    value = int(value)
                setattr(self._cfg, key, value)
        self._cfg.ram_gb = max(RAM_MIN, min(RAM_MAX, self._cfg.ram_gb))
        if "data_dir" in values:
            self._repo.game_dir = Path(self._cfg.data_dir)
        self._save()
        if "show_online" in values:
            if self._cfg.show_online:
                self._presence_last = (None, 0.0)
                self._presence_soon()
            else:
                threading.Thread(target=self.presence_offline, daemon=True).start()
        return asdict(self._cfg)

    # ================================================================= аккаунты
    def _activated(self, acc: Account) -> dict:
        self._accounts.save()
        if acc.kind in ("offline", "elyby"):
            threading.Thread(target=self._claim_nick, args=(acc.username,), daemon=True).start()
        self._presence_soon()
        return {"account": _account_dict(acc), "accounts": [_account_dict(a) for a in self._accounts.list()]}

    def password_strength(self, password: str) -> int:
        return password_strength(password)

    def _nick_key(self, acc: Account, password: str) -> None:
        """Пароль введён — ключ ника на сервере VaultMC теперь из ника и пароля (одинаковый на всех компьютерах)."""
        try:
            from core import skinserver
            skinserver.set_password(acc.username, password)
        except Exception as exc:  # noqa: BLE001
            _log(f"nick key {acc.username}: {exc}")

    def register_offline(self, nick: str, password: str, repeat: str) -> dict:
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        try:
            acc = self._accounts.register_offline(nick, password, repeat)
        except AuthError as exc:
            return {"error": str(exc)}
        self._nick_key(acc, password)
        return {**self._activated(acc), **self._new_recovery_code(acc)}

    # ---- код восстановления и смена ника (оффлайн-аккаунт) ----
    def _new_recovery_code(self, acc: Account) -> dict:
        """Выдать новый код восстановления: {"recoveryCode": ...} или {"recoveryError": ...} (нет связи — получить позже)."""
        from core import skinserver
        if acc.kind != "offline" or not skinserver.server_url():
            return {}
        code = skinserver.make_recovery_code()
        try:
            skinserver.set_recovery(acc.username, code)
        except Exception as exc:  # noqa: BLE001
            return {"recoveryError": f"Код восстановления не получен: {exc}. Получите его позже в «Настройках»"}
        return {"recoveryCode": code}

    def recovery_code(self) -> dict:
        acc = self._accounts.active
        if acc is None or acc.kind != "offline":
            return {"error": "Код восстановления — для оффлайн-аккаунта. Пароль Ely.by восстанавливается на сайте Ely.by"}
        r = self._new_recovery_code(acc)
        return {"code": r["recoveryCode"]} if "recoveryCode" in r else {"error": r.get("recoveryError", "Сервер VaultMC не настроен")}

    def recover_offline(self, nick: str, code: str, password: str, repeat: str) -> dict:
        """«Забыли пароль?»: ник + код восстановления + новый пароль. Работает и на новом ПК."""
        from core import skinserver
        from core.accounts import MIN_PASSWORD
        from core.utils import NICKNAME_RE
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        nick = (nick or "").strip()
        if not NICKNAME_RE.match(nick):
            return {"error": "Ник: 3–16 символов, латиница, цифры и _"}
        if len(password or "") < MIN_PASSWORD:
            return {"error": f"Пароль должен быть не короче {MIN_PASSWORD} символов"}
        if password != repeat:
            return {"error": "Пароли не совпадают"}
        try:
            skinserver.recover(nick, code, password)
            acc = self._accounts.reset_offline_password(nick, password, repeat)
        except (skinserver.SkinServerError, AuthError) as exc:
            return {"error": str(exc)}
        return {**self._activated(acc), **self._new_recovery_code(acc)}   # прежний код погашен — сразу новый

    def rename_account(self, new_nick: str, password: str) -> dict:
        """Новый ник оффлайн-аккаунта: сначала на сервере VaultMC (друзья, скин), потом в лаунчере."""
        from core import skinserver
        from core.utils import NICKNAME_RE
        acc = self._accounts.active
        if acc is None or acc.kind != "offline":
            return {"error": "Ник меняется только у оффлайн-аккаунта. Ник Ely.by — на сайте Ely.by"}
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        new_nick = (new_nick or "").strip()
        if not NICKNAME_RE.match(new_nick):
            return {"error": "Ник: 3–16 символов, латиница, цифры и _"}
        if new_nick == acc.username:
            return {"error": "Это ваш текущий ник"}
        try:
            self._accounts.check_password(acc.id, password)
        except AuthError as exc:
            return {"error": str(exc)}
        if not skinserver.server_url():
            return {"error": "Сервер VaultMC не настроен — ник сменить нельзя"}
        other = self._accounts._find_offline(new_nick)
        if other and other.id != acc.id:
            return {"error": "На этом компьютере уже есть оффлайн-аккаунт с таким ником"}
        old = acc.username
        try:
            skinserver.rename(old, new_nick, password)
            self._accounts.rename_offline(acc.id, new_nick)
        except (skinserver.SkinServerError, AuthError) as exc:
            return {"error": str(exc)}
        self._on_runner_event("log", f"[Аккаунт] ник {old} → {new_nick}")
        return self._activated(acc)

    def login_offline(self, nick: str, password: str) -> dict:
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        try:
            acc = self._accounts.login_offline(nick, password)
        except AuthError as exc:
            return {"error": str(exc)}
        self._nick_key(acc, password)
        return self._activated(acc)

    def enter_offline(self, nick: str, password: str, repeat: str = "") -> dict:
        """Одна форма «ник + пароль»: аккаунт есть на этом ПК — входим; нет — просим повторить пароль и создаём."""
        from core.accounts import MIN_PASSWORD
        from core.utils import NICKNAME_RE
        nick = (nick or "").strip()
        if self._accounts._find_offline(nick):
            return self.login_offline(nick, password)
        if not NICKNAME_RE.match(nick):
            return {"error": "Ник: 3–16 символов, латиница, цифры и _"}
        if len(password or "") < MIN_PASSWORD:
            return {"error": f"Пароль должен быть не короче {MIN_PASSWORD} символов"}
        if not repeat:
            return {"needRepeat": True}
        return self.register_offline(nick, password, repeat)

    def login_elyby(self, login: str, password: str, totp: str = "") -> dict:
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        try:
            acc = self._accounts.add_or_update(elyby.login(login, password, totp))
            return self._activated(acc)
        except elyby.TwoFactorRequired as exc:
            return {"error": str(exc), "need2fa": True}
        except AuthError as exc:
            return {"error": str(exc)}

    def elyby_register(self):
        webbrowser.open(elyby.REGISTER_URL)

    def elyby_oauth_start(self) -> dict:
        """Вход через сайт Ely.by: открываем страницу входа, результат придёт событием elyby_oauth."""
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}

        def wait():
            try:
                acc = self._accounts.add_or_update(elyby.oauth_login(webbrowser.open))
                self._emit("elyby_oauth", self._activated(acc))
            except Exception as exc:  # noqa: BLE001
                self._emit("elyby_oauth", {"error": str(exc)})
        threading.Thread(target=wait, daemon=True).start()
        return {"started": True}

    def ms_start(self) -> dict:
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        try:
            code = microsoft.start_device_login(self._cfg.ms_client_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        self._ms_cancel.clear()
        webbrowser.open(code.verification_uri)

        def wait():
            try:
                acc = self._accounts.add_or_update(microsoft.wait_for_login(self._cfg.ms_client_id, code,
                                                                            self._ms_cancel.is_set))
                self._emit("ms_login", self._activated(acc))
            except Exception as exc:  # noqa: BLE001
                self._emit("ms_login", {"error": str(exc)})
        threading.Thread(target=wait, daemon=True).start()
        return {"code": code.user_code, "url": code.verification_uri}

    def ms_cancel(self):
        self._ms_cancel.set()

    def _game_lock(self) -> bool:
        """Пока игра запускается или идёт, аккаунт не меняем: игра уже вошла под ним, а время игры и статус друзьям считаются по нему."""
        return self._runner.state in ("preparing", "running")

    def switch_account(self, account_id: str) -> dict:
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        acc = self._accounts.accounts.get(account_id)
        if acc is None:
            return {"error": "Аккаунт не найден"}
        if acc.kind == "offline" and not acc.remembered:   # пароль спрашиваем один раз на этом компьютере
            return {"needPassword": True, "username": acc.username}
        self._accounts.set_active(account_id)
        return self._activated(acc)

    def remove_account(self, account_id: str) -> dict:
        if self._game_lock() and self._accounts.active and self._accounts.active.id == account_id:
            return {"error": GAME_LOCK_MSG}
        self._accounts.remove(account_id)
        self._presence_soon()             # убрали активный аккаунт — друзья видят «не в сети»
        acc = self._accounts.active
        return {"account": _account_dict(acc) if acc else None,
                "accounts": [_account_dict(a) for a in self._accounts.list()]}

    def logout(self) -> dict:
        if self._game_lock():
            return {"error": GAME_LOCK_MSG}
        acc = self._accounts.active
        if acc is not None:
            acc.remembered = False        # «Выйти» — в следующий раз снова спросим пароль
        self._accounts.active_id = ""
        self._accounts.save()
        self._presence_soon()             # «Выйти» — друзья сразу видят «не в сети»
        return {"account": None}

    def change_password(self, old: str, new: str, repeat: str) -> dict:
        acc = self._accounts.active
        try:
            self._accounts.change_password(acc.id, old, new, repeat)
            try:   # replace — на сервере старый пароль перестаёт действовать (раньше оставался среди ключей ника)
                from core import skinserver
                skinserver.set_password(acc.username, new, replace=True)
            except Exception as exc:  # noqa: BLE001
                _log(f"nick key {acc.username}: {exc}")
            threading.Thread(target=self._claim_nick, args=(acc.username,), daemon=True).start()
            return {"ok": True}
        except (AuthError, AttributeError, KeyError) as exc:
            return {"error": str(exc)}

    # ---- скины ----
    def _pull_offline_skin(self, acc: Account, dest: Path) -> None:
        """Вошли с тем же ником на новом ПК: скина здесь нет, а на сервере VaultMC он есть — забираем.
        Один раз за запуск на аккаунт, чтобы не спрашивать сервер при каждой отрисовке головы."""
        tried = self.__dict__.setdefault("_skin_pulled", set())
        if acc.id in tried:
            return
        tried.add(acc.id)
        from core import skinserver
        data = skinserver.download_skin(acc.username)
        if data:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                self._on_runner_event("log", f"[Скины] скин {acc.username} взят с сервера VaultMC")
            except OSError as exc:
                _log(f"pull skin: {exc}")

    def get_skin(self, account_id: str = "") -> str:
        """Текстура скина (data:image/png) или "" — тогда интерфейс рисует свой силуэт."""
        acc = self._accounts.accounts.get(account_id) or self._accounts.active
        if acc is None:
            return ""
        custom = SKINS_DIR / f"{acc.id}.png"
        if acc.kind == "offline":
            if not custom.exists():
                self._pull_offline_skin(acc, custom)
            return self._data_url(custom) if custom.exists() else ""
        cache = SKIN_CACHE / f"{acc.id}.png"
        if not cache.exists() or time.time() - cache.stat().st_mtime > 3600:
            try:
                if acc.kind == "elyby":
                    url = f"https://skinsystem.ely.by/skins/{acc.username}.png"
                else:
                    profile = get_json("https://api.minecraftservices.com/minecraft/profile",
                                       headers={"Authorization": f"Bearer {acc.token()}"})
                    url = next((s["url"] for s in profile.get("skins", []) if s.get("state") == "ACTIVE"), "")
                if url:
                    cache.unlink(missing_ok=True)
                    download(url, cache)
            except Exception:  # noqa: BLE001 — без сети покажем кэш
                pass
        return self._data_url(cache) if cache.exists() else ""

    @staticmethod
    def _data_url(path: Path) -> str:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()

    def choose_offline_skin(self) -> str:
        acc = self._accounts.active
        path = self._dialog("open", file_types=("Скин Minecraft (*.png)",))
        if not path or acc is None:
            return ""
        # проверяем, что это скин: PNG 64×64, 64×32 или HD-кратные (128×128, 256×256...)
        head = Path(path).read_bytes()[:24]
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Это не PNG-картинка. Скин Minecraft — файл .png 64×64")
        w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        if w < 64 or w % 64 or h not in (w, w // 2):
            raise RuntimeError(f"Картинка {w}×{h} — не скин. Нужен скин Minecraft 64×64 (или 64×32, HD 128×128)")
        SKINS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, SKINS_DIR / f"{acc.id}.png")
        return self.get_skin(acc.id)

    def pick_skin_file(self) -> dict:
        """Выбор .png для мастерской: проверяет файл, но ничего не сохраняет (сохраняет кнопка «Сохранить»)."""
        path = self._dialog("open", file_types=("Скин Minecraft (*.png)",))
        if not path:
            return {}
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            return {"error": f"Не удалось прочитать файл: {exc}"}
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            return {"error": "Это не PNG-картинка. Скин Minecraft — файл .png 64×64"}
        if len(raw) > 256 * 1024:
            return {"error": "Файл больше 256 КБ — для скина это слишком много"}
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if w < 64 or w % 64 or w > 512 or h not in (w, w // 2):
            return {"error": f"Картинка {w}×{h} — не скин. Нужен скин Minecraft 64×64 (или 64×32, HD 128×128)"}
        return {"ok": True, "skin": "data:image/png;base64," + base64.b64encode(raw).decode()}

    # ---- студия скинов (оффлайн-аккаунт): скин, плащ, сервер скинов ----
    def _install_ely_prover(self) -> None:
        """Ник есть на Ely.by → сервер требует доказать владение. Доказываем аккаунтом Ely.by с тем же ником,
        если он добавлен в лаунчер (так оффлайн- и Ely.by-аккаунт одного игрока становятся одним ником)."""
        from core import elyby, skinserver

        def prove(name: str) -> str:
            acc = next((a for a in self._accounts.list() if a.kind == "elyby" and a.username.lower() == name.lower()), None)
            if acc is None:
                return ""
            proof = elyby.prove_ownership(acc)
            self._accounts.save()   # токен мог обновиться
            return proof
        skinserver.ELY_PROVER = prove

    # ---------------------------------------------------------------- статус «в сети» для друзей
    def _presence_state(self) -> dict | None:
        acc = self._accounts.active
        if acc is None or acc.kind not in ("offline", "elyby") or not self._cfg.show_online:
            return None
        playing = self._runner.state == "running"
        inst = self._instances.get(self._running_instance) if playing else None
        return {"state": "playing" if playing else "online",
                "instance": inst.name if inst else "", "version": inst.game_version if inst else "",
                "loader": inst.loader if inst else "", "room": self._room_address if playing and self._room_mode else "",
                "p2p": bool(playing and self._room_mode and self._p2p_hosting),
                "code": self._room_code if playing and self._room_mode else "",
                "relay": self._room_relay if playing and self._room_mode else None}

    def _presence_soon(self) -> None:
        """Отправить статус в фоне. Повторы и одинаковые статусы отсекаем — у бесплатного KV ~1000 записей в сутки."""
        def job():
            from core import skinserver
            with self.__dict__.setdefault("_presence_lock", threading.Lock()):
                acc = self._accounts.active
                state = self._presence_state()
                key = (acc.username if acc else "", json.dumps(state, sort_keys=True))
                last_key, last_at = getattr(self, "_presence_last", (None, 0.0))
                if key == last_key and time.time() - last_at < 25 * 60:
                    return
                prev_name = last_key[0] if last_key else ""
                try:
                    if prev_name and (acc is None or prev_name != acc.username):
                        skinserver.set_presence(prev_name, None)   # сменили аккаунт или вышли — старый «не в сети»
                    if acc and skinserver.server_url() and (state or prev_name == acc.username):
                        skinserver.set_presence(acc.username, state)
                    self._presence_last = (key[0], key[1]), time.time()
                except Exception as exc:  # noqa: BLE001
                    _log(f"presence: {exc}")
        threading.Thread(target=job, daemon=True).start()

    def _presence_loop(self) -> None:
        while True:   # раз в 25 минут подтверждаем «я в сети» (запись на сервере живёт 40 минут)
            time.sleep(25 * 60 + 5)
            self._presence_soon()

    def presence_offline(self) -> None:
        """Лаунчер закрывается — убираем статус сразу, не дожидаясь, пока он истечёт."""
        from core import skinserver
        acc = self._accounts.active
        if acc and skinserver.server_url() and acc.kind in ("offline", "elyby"):
            try:
                skinserver.set_presence(acc.username, None)
            except Exception:  # noqa: BLE001
                pass

    def _claim_nick(self, name: str) -> None:
        from core import skinserver
        try:
            skinserver.ensure_registered(name)
        except skinserver.SkinServerError as exc:
            _log(f"skin server claim {name}: {exc}")
            if exc.status == 409:   # ник занят другим игроком — говорим сразу, а не когда не заработают друзья
                self._emit("nick_problem", f"{exc}. Играть можно, но друзья и скины с этим ником работать не будут.")
        except Exception as exc:  # noqa: BLE001
            _log(f"skin server claim {name}: {exc}")

    def skin_studio(self) -> dict:
        """Всё для страницы «Скины»: текущий скин, плащ, тонкие руки, состояние сервера."""
        from core import skinserver
        acc = self._accounts.active
        if acc is None:
            return {"account": None}
        meta = self._skin_meta().get(acc.id, {})
        cape = CAPES_DIR / f"{acc.id}.png"
        return {"account": _account_dict(acc), "skin": self.get_skin(acc.id), "slim": bool(meta.get("slim")),
                "cape": self._data_url(cape) if cape.exists() else "", "server": bool(skinserver.server_url()),
                "editable": acc.kind == "offline"}

    def _skin_meta(self) -> dict:
        try:
            return json.loads((LAUNCHER_HOME / "skin_meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _png_from_data_url(data_url: str, kind: str) -> bytes:
        raw = base64.b64decode(str(data_url).split(",", 1)[-1])
        if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) > 256 * 1024:
            raise ValueError("Картинка повреждена")
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if kind == "skin" and not (w >= 64 and w % 64 == 0 and h in (w, w // 2)):
            raise ValueError(f"Картинка {w}×{h} — не скин")
        if kind == "cape" and not (w >= 64 and w % 64 == 0 and h == w // 2):
            raise ValueError(f"Картинка {w}×{h} — не плащ (нужно 64×32)")
        return raw

    def _sync_texture(self, acc: Account, kind: str, png: bytes | None, slim: bool = False) -> str:
        """Отправляет скин/плащ на сервер. Возвращает пустую строку или текст ошибки."""
        from core import skinserver
        if acc.kind != "offline" or not skinserver.server_url():
            return ""
        try:
            if png is None:
                skinserver.remove(acc.username, kind)
            else:
                skinserver.upload(acc.username, kind, png, slim)
            return ""
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    def save_skin(self, data_url: str, slim: bool = False) -> dict:
        acc = self._accounts.active
        if acc is None or acc.kind != "offline":
            return {"error": "Редактировать скин можно у оффлайн-аккаунта"}
        try:
            png = self._png_from_data_url(data_url, "skin")
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}
        SKINS_DIR.mkdir(parents=True, exist_ok=True)
        (SKINS_DIR / f"{acc.id}.png").write_bytes(png)
        meta = self._skin_meta()
        meta[acc.id] = {**meta.get(acc.id, {}), "slim": bool(slim)}
        (LAUNCHER_HOME / "skin_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        err = self._sync_texture(acc, "skin", png, bool(slim))
        return {"ok": True, "skin": self.get_skin(acc.id), "serverError": err}

    def reset_skin(self) -> dict:
        """Убирает свой скин: у вас — стандартный, на сервере скинов — удалён (плащ не трогаем)."""
        acc = self._accounts.active
        if acc is None or acc.kind != "offline":
            return {"error": "Сбросить скин можно у оффлайн-аккаунта"}
        try:
            (SKINS_DIR / f"{acc.id}.png").unlink(missing_ok=True)
        except OSError as exc:
            return {"error": f"Не удалось удалить файл скина: {exc}"}
        self.__dict__.setdefault("_skin_pulled", set()).add(acc.id)   # сброшенный скин не скачиваем обратно с сервера
        meta = self._skin_meta()
        if acc.id in meta:
            meta[acc.id] = {**meta[acc.id], "slim": False}
            (LAUNCHER_HOME / "skin_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return {"ok": True, "serverError": self._sync_texture(acc, "skin", None)}

    def save_cape(self, data_url: str = "") -> dict:
        acc = self._accounts.active
        if acc is None or acc.kind != "offline":
            return {"error": "Плащ можно поставить оффлайн-аккаунту"}
        path = CAPES_DIR / f"{acc.id}.png"
        if not data_url:
            path.unlink(missing_ok=True)
            return {"ok": True, "cape": "", "serverError": self._sync_texture(acc, "cape", None)}
        try:
            png = self._png_from_data_url(data_url, "cape")
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}
        CAPES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return {"ok": True, "cape": self._data_url(path), "serverError": self._sync_texture(acc, "cape", png)}

    def export_skin(self, data_url: str, filename: str = "skin.png") -> dict:
        try:
            png = self._png_from_data_url(data_url, "skin")
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}
        target = self._dialog("save", save_filename=Path(filename).name)
        if not target:
            return {}
        dest = Path(target)
        dest = dest if dest.suffix else dest.with_suffix(".png")
        dest.write_bytes(png)
        return {"ok": True, "path": str(dest)}

    def skin_from_elyby(self, nick: str) -> dict:
        """Скин игрока Ely.by по нику — через их официальную систему скинов (её разрешено использовать в проектах)."""
        nick = nick.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{2,16}", nick):
            return {"error": "Ник: латиница, цифры и _"}
        return self._fetch_skin(f"https://skinsystem.ely.by/skins/{nick}.png", f"У {nick} на Ely.by нет скина")

    def skin_from_url(self, url: str) -> dict:
        url = url.strip()
        if not re.match(r"^https?://", url):
            return {"error": "Нужна ссылка на картинку .png (http/https)"}
        return self._fetch_skin(url, "По ссылке нет картинки скина")

    def _fetch_skin(self, url: str, not_found: str) -> dict:
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"VaultMC/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read(256 * 1024 + 1)
        except Exception:  # noqa: BLE001
            return {"error": not_found}
        try:
            self._png_from_data_url("x," + base64.b64encode(raw).decode(), "skin")
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True, "skin": "data:image/png;base64," + base64.b64encode(raw).decode()}

    # ---- друзья через сервер VaultMC: заявки по нику, у обоих сразу ----
    def online_friends(self, action: str = "list", other: str = "") -> dict:
        from core import skinserver
        acc = self._accounts.active
        if not skinserver.server_url():
            return {"error": "no_server"}
        if acc is None:
            return {"error": "Войдите в аккаунт"}
        if acc.kind not in ("offline", "elyby"):
            return {"error": "Друзья по нику работают с оффлайн-аккаунтом и Ely.by — для других используйте код друга"}
        try:
            return skinserver.friends(acc.username, action, other.strip())
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def system_notify(self, key: str, title: str, text: str) -> dict:
        """Окошко Windows «Ник хочет в друзья» / «Ник открыл мир», когда лаунчер не на виду (core/winnotify.py).
        Не вышло показать — мигает значок на панели задач. Одно и то же уведомление не чаще раза в 10 минут."""
        if not self._cfg.win_notify:
            return {"skipped": "off"}
        now = time.time()
        sent = self.__dict__.setdefault("_notified", {})
        if now - sent.get(str(key), 0) < 600:
            return {"skipped": "duplicate"}
        sent[str(key)] = now

        def job():
            from core import winnotify
            from core.utils import resource_path
            result = winnotify.toast(str(title)[:120], str(text)[:240], "vaultmc://friends", str(resource_path("assets/logo_win.ico")))
            if result != "shown":
                _log(f"окошко Windows не показано ({result}) — мигаю значком")
                winnotify.flash_window(APP_NAME)
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True}

    # ================================================================= прямое подключение (P2P)
    def _p2p_host_start(self, port: int) -> None:
        """Мир открыт для сети — принимаем прямые подключения друзей (если сервер VaultMC доступен)."""
        from core import skinserver
        acc = self._accounts.active
        if not skinserver.server_url() or acc is None or acc.kind not in ("offline", "elyby"):
            return
        self._p2p.start()
        self._p2p.lan_port = port
        self._p2p_hosting = True
        self._on_runner_event("log", f"[Прямое подключение] Мир открыт на порту {port} — друзья могут зайти напрямую")
        self._emit("p2p_host", {"url": self._p2p.url})
        self._presence_soon()

    def _p2p_call(self, action: str, **extra) -> dict:
        from core import skinserver
        acc = self._accounts.active
        if acc is None or acc.kind not in ("offline", "elyby"):
            return {"error": "Прямое подключение работает с оффлайн-аккаунтом или Ely.by"}
        try:
            return skinserver.p2p(acc.username, action, **extra)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def p2p_log(self, text: str) -> None:
        """Строка диагностики прямого подключения — в консоль игры."""
        self._on_runner_event("log", f"[Прямое подключение] {str(text)[:300]}")

    def p2p_ice(self) -> dict:
        """TURN-серверы для WebRTC (ретранслятор при строгом NAT); пусто — работаем только через STUN."""
        from core import skinserver
        acc = self._accounts.active
        if acc is None or acc.kind not in ("offline", "elyby"):
            return {"servers": []}
        return {"servers": skinserver.turn_servers(acc.username)}

    def p2p_inbox(self) -> dict:
        return self._p2p_call("inbox") if self._p2p_hosting else {"offers": []}

    def p2p_answer(self, cid: str, sdp=None, error: str = "") -> dict:
        return self._p2p_call("answer", id=cid, sdp=sdp, error=error)

    def p2p_offer(self, host: str, cid: str, sdp, code: str = "") -> dict:
        return self._p2p_call("offer", host=host, id=cid, sdp=sdp, code=code)

    def p2p_poll(self, host: str, cid: str) -> dict:
        return self._p2p_call("poll", host=host, id=cid)

    def p2p_guest_prepare(self) -> dict:
        """Гость: поднимаем мост и порт на 127.0.0.1, к которому подключится игра."""
        self._p2p.start()
        return {"url": self._p2p.url, "port": self._p2p.guest_listen()}

    def p2p_guest_stop(self) -> None:
        self._p2p.stop_guest()

    def player_skin(self, nick: str) -> str:
        """Скин игрока с сервера VaultMC (для голов в списке друзей). Пусто — скина нет."""
        from core import skinserver
        url = skinserver.server_url()
        if not url or not re.fullmatch(r"[A-Za-z0-9_]{2,16}", nick or ""):
            return ""
        cache = self.__dict__.setdefault("_player_skins", {})
        hit = cache.get(nick.lower())
        if hit and time.time() - hit[1] < (1800 if hit[0] else 120):   # скин — полчаса; «нет скина/сбой» — 2 минуты
            return hit[0]
        try:
            import urllib.request
            with urllib.request.urlopen(f"{url}/skins/{nick}.png", timeout=8) as r:
                raw = r.read(256 * 1024)
            data = "data:image/png;base64," + base64.b64encode(raw).decode() if raw[:4] == b"\x89PNG" else ""
        except Exception:  # noqa: BLE001
            data = ""
        cache[nick.lower()] = (data, time.time())
        return data

    def open_skin_site(self):
        acc = self._accounts.active
        webbrowser.open("https://ely.by/skins" if not acc or acc.kind != "microsoft"
                        else "https://www.minecraft.net/msaprofile/mygames/editskin")

    # ================================================================= версии
    def versions_meta(self) -> dict:
        return {"latest": self._repo.latest, "offline": self._repo.offline, "fabricLoader": self._repo.fabric_loader,
                "count": len(self._repo.remote)}

    def refresh_versions(self):
        threading.Thread(target=self._refresh_versions_bg, daemon=True).start()

    def list_versions(self, loader: str, types: list, query: str = "", offset: int = 0, limit: int = 40,
                      oldest: bool = False) -> dict:
        items = self._repo.items(loader, set(types), query)
        if oldest:
            items = list(reversed(items))
        counts: dict = {}
        for i in self._repo.items(loader, {"release", "snapshot", "old_beta", "old_alpha"}, query):
            entry = self._repo.remote_entry(i.game_version) if i.type in ("fabric", "forge", "neoforge") else None
            kind = entry.type if entry else i.type
            counts[kind] = counts.get(kind, 0) + 1
        return {"total": len(items), "counts": counts, "items": [{
            "id": i.id, "game": i.game_version, "type": i.type, "typeLabel": TYPE_LABELS.get(i.type, i.type),
            "date": i.release_time[:10], "installed": i.installed} for i in items[offset:offset + limit]]}

    def loader_builds(self, loader: str, game: str) -> list:
        """Сборки Forge/NeoForge для версии игры: [{version, id, recommended}]."""
        from core.forge import loader_id
        builds = self._repo.loader_builds(loader, game)
        return [{"version": v, "id": loader_id(loader, game, v), "recommended": n == 0} for n, v in enumerate(builds)]

    # ================================================================= сборки
    def list_instances(self) -> list:
        return [_instance_dict(i, self._repo.game_dir) for i in self._instances.list()]

    def create_instance(self, name: str, version_id: str, game_version: str, loader: str) -> dict:
        inst = self._instances.create(name, version_id, game_version, loader)
        self._cfg.selected_instance = inst.id
        self._save()
        self._runner.install(version_id)  # сразу начинаем скачивать игру
        return _instance_dict(inst, self._repo.game_dir)

    def select_instance(self, instance_id: str):
        self._cfg.selected_instance = instance_id
        self._save()

    def update_instance(self, instance_id: str, values: dict) -> dict:
        inst = self._inst(instance_id)
        if "name" in values and not str(values["name"]).strip():
            values = {k: v for k, v in values.items() if k != "name"}     # пустое название не сохраняем — карточка осталась бы безымянной
        for key in ("name", "ram_gb", "jvm_args", "notes", "color"):
            if key in values:
                setattr(inst, key, int(values[key]) if key == "ram_gb" else values[key])
        self._instances.save(inst)
        return _instance_dict(inst, self._repo.game_dir)

    def set_instance_icon(self, instance_id: str, data_url: str) -> dict:
        """Сохраняет картинку сборки (интерфейс уже уменьшил её до 128×128 PNG). Пустая строка — убрать картинку."""
        inst = self._inst(instance_id)
        path = inst.root / "icon.png"
        if not data_url:
            path.unlink(missing_ok=True)
        else:
            prefix = "data:image/png;base64,"
            if not data_url.startswith(prefix):
                return {"error": "Нужна картинка PNG"}
            raw = base64.b64decode(data_url[len(prefix):])
            if len(raw) > 1_000_000:
                return {"error": "Картинка слишком большая"}
            path.write_bytes(raw)
        return _instance_dict(inst, self._repo.game_dir)

    def delete_instance(self, instance_id: str):
        if self._runner.state in ("running", "preparing", "installing") and self._running_instance == instance_id:
            raise RuntimeError("Сначала закройте игру этой сборки")
        self._instances.delete(self._inst(instance_id))
        if self._cfg.selected_instance == instance_id:
            self._cfg.selected_instance = ""
            self._save()

    def duplicate_instance(self, instance_id: str) -> dict:
        return _instance_dict(self._instances.duplicate(self._inst(instance_id)), self._repo.game_dir)

    def backup_worlds(self, instance_id: str) -> dict:
        if self._instance_running(instance_id):
            return {"error": "Закройте игру — мир может быть открыт, и копия получилась бы неполной"}
        path = self._instances.backup_worlds(self._inst(instance_id))
        return {"path": str(path) if path else ""}

    def export_formats(self) -> dict:
        return {k: v[0] for k, v in EXPORT_FORMATS.items()}

    def export_instance(self, instance_id: str, fmt: str = "mrpack") -> dict:
        inst = self._inst(instance_id)
        label, ext = EXPORT_FORMATS.get(fmt, EXPORT_FORMATS["mrpack"])
        target = self._dialog("save", save_filename=f"{inst.name}{ext}", file_types=(f"{label} (*{ext})",))
        if not target:
            return {}
        path = export_any(fmt, inst, Path(target), self._instances, self._cf_key,
                          lambda m: self._on_runner_event("log", m))
        return {"path": str(path)}

    def _open_pack_loop(self) -> None:
        """Сборку открыли двойным щелчком, пока лаунчер работает: main.py оставляет путь в open_pack.txt,
        а окно каждые 3 с получает сигнал и забирает его через take_open_pack."""
        while True:
            time.sleep(3)
            try:
                if OPEN_PACK_FILE.exists():
                    self._bus.emit("open_pack", "")
            except Exception:  # noqa: BLE001
                pass

    def take_open_pack(self) -> dict:
        """Забирает путь к сборке, открытой из проводника, и запускает импорт. Пусто — открывать нечего."""
        try:
            path = OPEN_PACK_FILE.read_text(encoding="utf-8").strip()
            OPEN_PACK_FILE.unlink(missing_ok=True)
        except OSError:
            return {}
        code = re.fullmatch(r"code:([A-Z0-9]{6})", path or "")
        if code:                       # ссылка на сборку с сайта — окно покажет «Сборка по коду» с этим кодом
            return {"code": code.group(1)}
        if path == "page:friends":     # нажали на окошко Windows о друге (vaultmc://friends)
            return {"page": "friends"}
        if not path or not Path(path).is_file():
            return {}
        return self.import_file(path)

    def import_instance(self) -> dict:
        path = self._dialog("open", file_types=("Сборки (*.mrpack;*.zip;*.vaultpack;*.neonpack)",))
        if not path:
            return {}
        return self.import_file(path)

    def import_file(self, path: str) -> dict:
        def job():
            inst, problems = import_any(Path(path), self._instances, self._cf_key,
                                        lambda m: self._on_runner_event("log", m))
            self._sync_mods_bg(inst)
            return {**_instance_dict(inst, self._repo.game_dir), "problems": problems}
        started = self._runner.run_task(f"Импорт {Path(path).name}...", job, "modpack_installed")
        return {"started": True} if started else {"error": "Дождитесь окончания текущей операции"}

    def share_instance(self, instance_id: str) -> dict:
        """Загружает описание сборки на сервер VaultMC и возвращает код из 6 символов (живёт 30 дней)."""
        from core import skinserver
        from core.sharecode import build_pack
        if not skinserver.server_url():
            return {"error": "Сервер VaultMC не настроен"}
        acc = self._accounts.active
        if acc is None or acc.kind not in ("offline", "elyby"):
            return {"error": "Код сборки работает с оффлайн-аккаунтом или Ely.by — для других аккаунтов используйте экспорт в файл"}
        inst = self._inst(instance_id)
        if inst.loader == "quilt":
            return {"error": "Сборки на Quilt пока нельзя передать кодом — используйте экспорт в файл"}
        try:
            pack, manual = build_pack(inst, lambda m: self._on_runner_event("log", m))
            code = skinserver.share_pack(acc.username, pack)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        if not code:
            return {"error": "Сервер не выдал код"}
        mods = len(pack["index"].get("files") or [])
        return {"code": code, "mods": mods, "manual": manual}

    def import_by_code(self, code: str) -> dict:
        from core import skinserver
        from core.sharecode import install_pack as install_shared
        code = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
        if len(code) != 6:
            return {"error": "Код сборки — 6 букв и цифр, например K7XQ2M"}
        try:
            data = skinserver.get_pack(code)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        pack = data.get("pack") or {}

        def job():
            log = lambda m: self._on_runner_event("log", m)  # noqa: E731
            log(f"[Код сборки] {code}: «{pack.get('name', '?')}» от {data.get('by', '?')}")
            inst, problems = install_shared(pack, self._instances, self._cf_key, log)
            self._sync_mods_bg(inst)
            return {**_instance_dict(inst, self._repo.game_dir), "problems": problems}
        started = self._runner.run_task(f"Сборка по коду {code}...", job, "modpack_installed")
        return {"started": True, "name": pack.get("name", ""), "by": data.get("by", "")} if started \
            else {"error": "Дождитесь окончания текущей операции"}

    def open_folder(self, kind: str, instance_id: str = ""):
        if kind == "data":
            path = self._repo.game_dir
        elif kind == "launcher":
            path = LAUNCHER_HOME
        else:
            inst = self._inst(instance_id)
            if kind == "backups":
                path = inst.root / "backups"
                path.mkdir(parents=True, exist_ok=True)
            else:
                path = inst.game_dir / kind if kind in ("mods", "saves", "screenshots", "resourcepacks",
                                                        "shaderpacks", "logs", "crash-reports") else inst.game_dir
        open_in_file_manager(path)

    # ---- миры, серверы, скриншоты ----
    def worlds(self, instance_id: str) -> dict:
        from core import worlds as W
        inst = self._inst(instance_id)
        W.purge_trash(inst.root / "trash")      # миры старше недели из корзины удаляются насовсем
        return {"worlds": W.list_worlds(inst.game_dir), "backups": W.list_backups(inst.root / "backups"),
                "trash": W.list_trash(inst.root / "trash"), "running": self._instance_running(instance_id),
                "auto": self._cfg.auto_world_backups}

    def world_action(self, instance_id: str, action: str, arg: str = "", target: str = "") -> dict:
        from core import worlds as W
        inst = self._inst(instance_id)
        if self._instance_running(inst.id) and action in ("restore", "delete", "backup", "trash_restore"):
            # копия открытого мира может оказаться битой: игра как раз может записывать его файлы
            return {"error": "Закройте игру — мир может быть открыт, и копия получилась бы неполной"
                    if action == "backup" else "Закройте игру — мир сейчас открыт"}
        try:
            if action == "backup":
                started = self._runner.run_task(
                    "Резервная копия мира...",
                    lambda: {"instance": inst.id, "file": W.backup_world(inst.game_dir, arg, inst.root / "backups").name},
                    "world_backup_done")
                return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}
            if action == "restore":
                return {"ok": True, "folder": W.restore_backup(inst.game_dir, inst.root / "backups", arg)}
            if action == "copy":
                dest = self._inst(target)
                return {"ok": True, "folder": W.copy_world(inst.game_dir, arg, dest.game_dir), "target": dest.name}
            if action == "delete":            # мир уходит в корзину на 7 дней, а не стирается
                return {"ok": True, "entry": W.trash_world(inst.game_dir, arg, inst.root / "trash")}
            if action == "trash_restore":
                return {"ok": True, "folder": W.restore_trash(inst.game_dir, inst.root / "trash", arg)}
            if action == "trash_delete":
                W.delete_trash(inst.root / "trash", arg)
                return {"ok": True}
            if action == "delete_backup":
                (inst.root / "backups" / Path(arg).name).unlink(missing_ok=True)
                return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"error": "Неизвестное действие"}

    def servers(self, instance_id: str) -> list:
        from core.worlds import list_servers
        return list_servers(self._inst(instance_id).game_dir)

    def save_servers(self, instance_id: str, servers: list) -> list:
        from core.worlds import list_servers, save_servers
        inst = self._inst(instance_id)
        clean = [{"name": str(s.get("name", ""))[:64], "ip": str(s.get("ip", "")).strip()[:255]}
                 for s in servers if str(s.get("ip", "")).strip()]
        save_servers(inst.game_dir, clean)
        return list_servers(inst.game_dir)

    def check_address(self, address: str) -> dict:
        """Перед подключением к комнате/серверу: находится ли адрес. «Unknown host» в игре = адрес не нашёлся."""
        import socket
        clean = address.strip().replace(" ", "")
        host = clean.rsplit(":", 1)[0] if clean.count(":") == 1 else clean
        if not host or "." not in host:
            return {"ok": False, "reason": "typo", "address": clean}
        try:
            socket.getaddrinfo(host, None)
            return {"ok": True, "address": clean}
        except OSError:
            pass
        # системный DNS не нашёл — проверим через DNS Cloudflare (по HTTPS): если там адрес есть, виноват провайдер
        try:
            from urllib.parse import quote
            data = get_json(f"https://cloudflare-dns.com/dns-query?name={quote(host, safe='')}&type=A",
                            headers={"Accept": "application/dns-json"}, timeout=6, attempts=1)
            if data.get("Answer"):
                return {"ok": False, "reason": "dns", "address": clean}
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": "closed", "address": clean}

    def server_ping(self, address: str) -> dict:
        from core.worlds import ping
        return ping(address)

    def screenshots(self, instance_id: str) -> list:
        from core.worlds import list_screenshots
        return list_screenshots(self._inst(instance_id).game_dir)

    def screenshot(self, instance_id: str, file: str) -> str:
        from core.worlds import screenshot_data
        try:
            return screenshot_data(self._inst(instance_id).game_dir, file)
        except OSError:
            return ""

    def save_screenshot(self, instance_id: str, file: str) -> dict:
        """Сохранить скриншот куда-нибудь (окно «Сохранить как»)."""
        src = self._inst(instance_id).game_dir / "screenshots" / Path(file).name
        if not src.is_file():
            return {"error": "Скриншот не найден"}
        target = self._dialog("save", save_filename=src.name)
        if not target:
            return {}
        dest = Path(target)
        if not dest.suffix:
            dest = dest.with_suffix(src.suffix)
        shutil.copy2(src, dest)
        return {"ok": True, "path": str(dest)}

    def delete_screenshot(self, instance_id: str, file: str) -> list:
        from core.worlds import list_screenshots
        inst = self._inst(instance_id)
        (inst.game_dir / "screenshots" / Path(file).name).unlink(missing_ok=True)
        return list_screenshots(inst.game_dir)

    # ---- моды сборки ----
    def instance_mods(self, instance_id: str) -> list:
        inst = self._inst(instance_id)
        index = ModIndex(inst.game_dir).data
        titles = {v["file"]: v["title"] for v in index.values()}
        return [{"file": m.path.name, "name": titles.get(m.display_name, m.display_name), "enabled": m.enabled,
                 "size": m.size_kb} for m in ModManager(inst.game_dir).list_mods()]

    def toggle_mod(self, instance_id: str, filename: str) -> list:
        manager = ModManager(self._inst(instance_id).game_dir)
        mod = next((m for m in manager.list_mods() if m.path.name == filename), None)
        if mod is None:  # файл уже удалили или переименовали вручную — просто обновляем список
            return self.instance_mods(instance_id)
        try:
            manager.toggle(mod)
        except OSError:
            raise ValueError("Файл занят — закройте игру и попробуйте снова") from None
        return self.instance_mods(instance_id)

    def check_mod_updates(self, instance_id: str) -> dict:
        """Какие моды сборки можно обновить (Modrinth + CurseForge)."""
        from core.modupdates import check
        inst = self._inst(instance_id)
        if not inst.supports_mods:
            return {"updates": []}
        return {"updates": check(inst.game_dir, inst.game_version, inst.loader, self._cf_key,
                                 lambda m: self._on_runner_event("log", m))}

    def apply_mod_updates(self, instance_id: str, updates: list) -> dict:
        from core.modupdates import apply
        inst = self._inst(instance_id)

        def job():
            self._auto_snapshot(inst, f"Перед обновлением модов ({len(updates)})")
            return {"instance": inst.id, "result": apply(inst.game_dir, updates, lambda m: self._on_runner_event("log", m))}
        started = self._runner.run_task(f"Обновление модов ({len(updates)})...", job, "mods_updated")
        return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}

    def mod_changelog(self, source: str, project_id: str, version_id: str) -> dict:
        """«Что нового» в версии мода — текст с Modrinth или CurseForge (подгружается по клику)."""
        from core.modupdates import changelog
        try:
            return {"text": changelog(source, project_id, version_id, self._cf_key)}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Не удалось загрузить список изменений: {exc}"}

    def mod_backups(self, instance_id: str) -> list:
        """Старые версии модов (после обновлений), к которым можно откатиться."""
        from core.modupdates import list_backups
        return list_backups(self._inst(instance_id).game_dir)

    def rollback_mod(self, instance_id: str, backup_id: str) -> dict:
        """Откат одного мода к версии до обновления."""
        from core.modupdates import rollback
        inst = self._inst(instance_id)
        if self._runner.busy:
            return {"error": "Дождитесь окончания текущей операции"}
        self._auto_snapshot(inst, "Перед откатом мода")
        try:
            title = rollback(inst.game_dir, str(backup_id))
        except (ValueError, OSError) as exc:
            return {"error": str(exc)}
        return {"ok": True, "title": title}

    # ---- снимки сборки (core/snapshots.py): точки, к которым можно вернуть моды и настройки ----
    def _auto_snapshot(self, inst, reason: str) -> None:
        """Снимок перед рискованным действием. Не вышло (диск, занятый файл) — действие всё равно выполняется."""
        from core import snapshots
        try:
            snapshots.create(inst.root, inst.game_dir, reason)
        except Exception as exc:  # noqa: BLE001
            self._on_runner_event("log", f"[Снимки] не удалось сохранить точку «{reason}»: {exc}")

    def snapshots(self, instance_id: str) -> dict:
        from core import snapshots
        inst = self._inst(instance_id)
        try:
            pending = snapshots.pending_changes(inst.root, inst.game_dir)
        except OSError:
            pending = None
        return {"items": snapshots.history(inst.root), "pending": pending,
                "running": self._instance_running(instance_id)}

    def snapshot_create(self, instance_id: str, note: str = "") -> dict:
        from core import snapshots
        inst = self._inst(instance_id)
        reason = (str(note).strip() or "Точка, сохранённая вручную")[:120]
        started = self._runner.run_task("Сохраняю точку сборки...",
                                        lambda: {"instance": inst.id, **snapshots.create(inst.root, inst.game_dir, reason)},
                                        "snapshot_done")
        return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}

    def snapshot_restore(self, instance_id: str, snap_id: str) -> dict:
        from core import snapshots
        inst = self._inst(instance_id)
        if self._instance_running(instance_id):
            return {"error": "Закройте игру — пока она открыта, файлы модов заняты"}
        started = self._runner.run_task("Возвращаю сборку к точке...",
                                        lambda: {"instance": inst.id, **snapshots.restore(inst.root, inst.game_dir, str(snap_id))},
                                        "snapshot_restored")
        return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}

    def snapshot_delete(self, instance_id: str, snap_id: str) -> dict:
        from core import snapshots
        if self._runner.busy:
            return {"error": "Дождитесь окончания текущей операции"}
        snapshots.delete(self._inst(instance_id).root, str(snap_id))
        return {"ok": True}

    def delete_mod(self, instance_id: str, filename: str) -> list:
        manager = ModManager(self._inst(instance_id).game_dir)
        mod = next((m for m in manager.list_mods() if m.path.name == filename), None)
        if mod is None:  # файл уже удалили или переименовали вручную — просто обновляем список
            return self.instance_mods(instance_id)
        index = ModIndex(manager.game_dir)
        key = index.key_for_file(mod.path.name)
        try:
            manager.delete(mod)
        except OSError:
            raise ValueError("Файл занят — закройте игру и попробуйте снова") from None
        # запись индекса убираем и запоминаем «удалено сами», иначе авто-установка вернёт мод при следующем запуске
        if key:
            index.data.pop(key, None)
            index.path.write_text(json.dumps(index.data, indent=2, ensure_ascii=False), encoding="utf-8")
        index.mark_removed(*( [key] if key else []), "file:" + mod.path.name.removesuffix(".disabled"))
        return self.instance_mods(instance_id)

    def add_mod_files(self, instance_id: str) -> list:
        files = self._dialog("open", allow_multiple=True, file_types=("Моды (*.jar)",)) or []
        inst = self._inst(instance_id)
        manager = ModManager(inst.game_dir)
        index = ModIndex(inst.game_dir)
        for f in files:
            manager.install(Path(f))
            index.unmark_removed("file:" + Path(f).name.removesuffix(".disabled"))
        return self.instance_mods(instance_id)

    # ---- ресурспаки и шейдеры ----
    def packs_list(self, instance_id: str) -> dict:
        from core import packs
        inst = self._inst(instance_id)
        return {"resourcepacks": packs.list_resourcepacks(inst.game_dir),
                "shaders": packs.list_shaders(inst.game_dir),
                "shaders_on": packs.shader_state(inst.game_dir)["on"],
                "running": self._instance_running(instance_id)}

    def pack_action(self, instance_id: str, action: str, kind: str = "resourcepack", name: str = "",
                    arg=None) -> dict:
        """action: toggle (arg — true/false) | move (arg — -1 выше, 1 ниже) | shader (name — '' выключить) |
        delete | add (диалог выбора файлов)."""
        from core import packs
        inst = self._inst(instance_id)
        if self._instance_running(instance_id):
            return {"error": "Закройте игру: при выходе она сохранит свои настройки поверх новых"}
        try:
            if action == "toggle":
                packs.set_resourcepack(inst.game_dir, name, bool(arg))
            elif action == "move":
                packs.move_resourcepack(inst.game_dir, name, int(arg or 0))
            elif action == "shader":
                packs.set_shader(inst.game_dir, name)
            elif action == "delete":
                packs.delete(inst.game_dir, kind, name)
            elif action == "add":
                if kind == "shader":
                    files = self._dialog("open", allow_multiple=True, file_types=("Шейдеры (*.zip)",)) or []
                else:
                    files = self._dialog("open", allow_multiple=True, file_types=("Ресурспаки (*.zip)",)) or []
                if not files:
                    return {"ok": ""}
                added = packs.add_files(inst.game_dir, kind, files)
                return {"ok": "Добавлено: " + ", ".join(added)}
            else:
                return {"error": "Неизвестное действие"}
        except (ValueError, OSError) as exc:
            return {"error": str(exc) or "Не получилось"}
        return {"ok": ""}

    def packs_check_updates(self, instance_id: str) -> dict:
        from core import packs
        inst = self._inst(instance_id)
        ups = []
        for kind in ("resourcepack", "shader"):
            ups += packs.check_updates(inst.game_dir, kind, inst.game_version,
                                       lambda m: self._on_runner_event("log", m))
        return {"updates": ups}

    def packs_apply_updates(self, instance_id: str, updates: list) -> dict:
        from core import packs
        inst = self._inst(instance_id)
        if self._instance_running(instance_id):
            return {"error": "Закройте игру, потом обновляйте"}

        def job():
            self._auto_snapshot(inst, f"Перед обновлением ресурспаков и шейдеров ({len(updates)})")
            done, errors = 0, []
            for kind in ("resourcepack", "shader"):
                mine = [u for u in updates if isinstance(u, dict) and u.get("kind") == kind]
                if mine:
                    d, e = packs.apply_updates(inst.game_dir, kind, mine, lambda m: self._on_runner_event("log", m))
                    done, errors = done + d, errors + e
            return {"instance": inst.id, "done": done, "errors": errors}
        started = self._runner.run_task(f"Обновление ресурспаков ({len(updates)})...", job, "packs_updated")
        return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}

    # ---- оптимизация ----
    def _instance_running(self, instance_id: str) -> bool:
        """Игра этой сборки сейчас запущена — options.txt она перезапишет при выходе, менять его бесполезно."""
        return self._runner.state in ("preparing", "running") and getattr(self, "_running_instance", None) == instance_id

    def apply_preset(self, instance_id: str, preset: str) -> dict:
        if self._instance_running(instance_id):
            return {"error": "Закройте игру: при выходе она сохранит свои настройки графики поверх новых"}
        apply_preset(self._inst(instance_id).game_dir, preset)
        return {"ok": True}

    def _sync_mods(self, inst) -> int:
        """Узнаём моды, которые лежат в сборке, но лаунчер о них не знал (импорт, ручное копирование)."""
        try:
            n = sync_index(inst.game_dir, self._cf_key, lambda m: self._on_runner_event("log", m))
            if n:
                self._on_runner_event("log", f"[Моды] {inst.name}: распознано модов — {n}")
            return n
        except Exception as exc:  # noqa: BLE001
            _log(f"sync_index: {exc}")
            return 0

    def _sync_mods_bg(self, inst) -> None:
        """То же в фоне: интерфейс не ждёт. Когда распознаем новые моды — интерфейс обновит отметки."""
        running = self.__dict__.setdefault("_syncing", set())
        if inst.id in running:
            return
        running.add(inst.id)

        def job():
            try:
                if self._sync_mods(inst):
                    self._bus.emit("mods_synced", inst.id)
            finally:
                running.discard(inst.id)
        threading.Thread(target=job, daemon=True).start()

    def optimization_status(self, instance_id: str) -> dict:
        """Какие пункты набора оптимизации уже стоят в сборке (любым из модов-заменителей)."""
        inst = self._inst(instance_id)
        self._sync_mods_bg(inst)
        roles = {role: optimization_candidates(role, inst.loader) for role in OPTIMIZATION_PACK}
        slugs = sorted({slug for c in roles.values() for slug in c})
        cache = getattr(self, "_opt_ids", None) or {}
        if not all(slug in cache for slug in slugs):
            try:
                from urllib.parse import urlencode
                import json as _json
                from core.modsources import MODRINTH_API
                projects = get_json(f"{MODRINTH_API}/projects?{urlencode({'ids': _json.dumps(slugs)})}")
                cache.update({p["slug"]: p["id"] for p in projects})
                self._opt_ids = cache
            except Exception:  # noqa: BLE001
                pass
        index = ModIndex(inst.game_dir)

        def has(slug: str) -> bool:
            if slug in cache and index.has("modrinth", cache[slug]):
                return True
            return any(isinstance(e, dict) and e.get("slug") == slug and index.has("modrinth", k.split(":", 1)[1])
                       for k, e in index.data.items() if k.startswith("modrinth:"))
        return {"installed": [role for role, cands in roles.items() if any(has(s) for s in cands)]}

    # ================================================================= диагностика (этап 6 плана 1.0)
    def _instance_java_major(self, inst) -> int:
        try:
            return int(self._repo.load_version_json(inst.version_id).get("javaVersion", {}).get("majorVersion", 8))
        except Exception:  # noqa: BLE001  версия ещё не скачана
            return 0

    def _instance_ram(self, inst) -> int:
        return inst.ram_gb or self._auto_ram(inst)

    def jvm_explain(self, instance_id: str) -> dict:
        """«Какие флаги Java использует лаунчер и почему» — по тем же правилам, что и при запуске."""
        from core.jvmreport import explain
        inst = self._inst(instance_id)
        own = f"{self._cfg.extra_jvm_args} {inst.jvm_args}"
        return explain(self._instance_java_major(inst), self._instance_ram(inst), os.cpu_count() or 0, total_ram_gb() or 0,
                       self._cfg.optimize_jvm, inst.compat, own)

    def lag_check(self, instance_id: str) -> dict:
        """«Почему тормозит»: разбор журналов последнего сеанса игры (FPS лаунчер не измеряет)."""
        from core import lagcheck
        inst = self._inst(instance_id)
        game_log = lagcheck.read_game_log(inst.game_dir)
        gc_log = lagcheck._read_tail(inst.game_dir / "logs" / "vaultmc-gc.log")
        mods = sum(1 for p in inst.mods_dir.iterdir() if p.suffix == ".jar") if inst.mods_dir.is_dir() else 0
        res = lagcheck.analyze(game_log, gc_log, self._instance_ram(inst), self._instance_java_major(inst), mods)
        return {**res, "ram": self._instance_ram(inst), "running": self._instance_running(instance_id)}

    def net_check_start(self) -> dict:
        """Проверка сети в фоне (до ~15 с); итог — событие netcheck_done. Кнопка «Играть» не блокируется."""
        if getattr(self, "_net_busy", False):
            return {"started": True}
        self._net_busy = True

        def job():
            from core import netcheck
            try:
                res = netcheck.run()
                self._last_net = res
                self._emit("netcheck_done", res)
            except Exception as exc:  # noqa: BLE001
                self._emit("netcheck_done", {"error": f"Проверка не удалась: {exc}"})
            finally:
                self._net_busy = False
        threading.Thread(target=job, daemon=True).start()
        return {"started": True}

    def _report_info(self, inst) -> dict:
        import platform
        from core.autotune import detect_pc
        from core.jvmreport import explain
        from core.supportreport import SAFE_CONFIG
        pc = detect_pc()
        java = self._instance_java_major(inst)
        ram = self._instance_ram(inst)
        jvm = explain(java, ram, os.cpu_count() or 0, total_ram_gb() or 0, self._cfg.optimize_jvm, inst.compat,
                      f"{self._cfg.extra_jvm_args} {inst.jvm_args}")
        mods = [f"{'  ' if m.enabled else '(выкл) '}{m.path.name}" for m in ModManager(inst.game_dir).list_mods()]
        cfg = asdict(self._cfg)
        safe_cfg = [f"{k} = {cfg.get(k)}" for k in SAFE_CONFIG if k in cfg]
        acc = self._accounts.active
        return {"launcher": APP_VERSION, "sections": [
            ("Лаунчер", [f"VaultMC {APP_VERSION}", f"Система: {platform.platform()}", f"Python {platform.python_version()}"]),
            ("Компьютер", [f"Память: {pc.get('ram_gb')} ГБ", f"Процессор: {pc.get('cpu')} ({pc.get('cores')} потоков)"] +
             [f"Видеокарта: {g['name']} ({g.get('vram_gb') or '?'} ГБ)" for g in pc.get("gpus", [])]),
            ("Сборка", [f"{inst.name}: Minecraft {inst.game_version}, {inst.loader}", f"Нужна Java {java or '?'}; игре выделено {ram} ГБ",
                        f"Режим совместимости: {'да' if inst.compat else 'нет'}", f"Аккаунт: {acc.kind if acc else 'нет'}"]),
            ("Флаги Java (как лаунчер запустит игру)", jvm["lines"] + [""] + jvm["flags"]),
            (f"Моды ({len(mods)})", mods or ["нет"]),
            ("Настройки лаунчера", safe_cfg),
        ]}

    def _build_report(self, inst, hide_nick: bool = True) -> tuple[dict, dict]:
        from core import supportreport as sr
        acc = self._accounts.active
        names = sr.windows_names() + ((acc.username,) if hide_nick and acc else ())
        net = getattr(self, "_last_net", None)
        return sr.collect(inst.game_dir, LAUNCHER_HOME, self._report_info(inst), list(self._logs), names,
                          (net or {}).get("text", ""))

    def support_report_preview(self, instance_id: str, hide_nick: bool = True) -> dict:
        """Что попадёт в отчёт для поддержки (уже без личного) — игрок смотрит до сохранения."""
        files, stats = self._build_report(self._inst(instance_id), hide_nick)
        return {"files": [{"name": n, "size": len(t.encode("utf-8"))} for n, t in files.items()], "removed": stats,
                "net": bool(getattr(self, "_last_net", None))}

    def support_report_save(self, instance_id: str, hide_nick: bool = True) -> dict:
        from core import supportreport as sr
        inst = self._inst(instance_id)
        files, stats = self._build_report(inst, hide_nick)
        target = self._dialog("save", save_filename=f"VaultMC-report-{time.strftime('%Y-%m-%d')}.zip", file_types=("Архив отчёта (*.zip)",))
        if not target:
            return {}
        dest = Path(target)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        try:
            dest.write_bytes(sr.build_zip(files))
        except OSError as exc:
            return {"error": f"Не удалось сохранить файл: {exc}"}
        return {"ok": True, "path": str(dest), "files": len(files)}

    def autotune_scan(self, instance_id: str) -> dict:
        """Смотрим железо и предлагаем настройки для сборки (ничего не меняем)."""
        from core.autotune import detect_pc, plan_for
        inst = self._inst(instance_id)
        pc = detect_pc()
        mods = sum(1 for p in inst.mods_dir.iterdir() if p.suffix == ".jar") if inst.mods_dir.is_dir() else 0
        plan = plan_for(pc, mods, inst.loader)
        self._autotune_plan = (inst.id, plan)
        return {"pc": pc, "plan": {k: v for k, v in plan.items() if k != "options"}}

    def autotune_apply(self, instance_id: str) -> dict:
        from core.autotune import apply_options
        inst = self._inst(instance_id)
        if self._instance_running(inst.id):
            return {"error": "Закройте игру: при выходе она сохранит свои настройки графики поверх новых"}
        saved = getattr(self, "_autotune_plan", None)
        if not saved or saved[0] != inst.id:
            self.autotune_scan(instance_id)
            saved = self._autotune_plan
        plan = saved[1]
        inst.ram_gb = int(plan["ram_gb"])
        self._instances.save(inst)
        apply_options(inst.game_dir, plan["options"])
        if plan.get("discrete_gpu") and not self._cfg.discrete_gpu:
            self._cfg.discrete_gpu = True
            self._save()
        self._on_runner_event("log", f"[Автонастройка] {inst.name}: ПК «{plan['tier_label']}», "
                                     f"память {plan['ram_gb']} ГБ, прорисовка {plan['options'].get('renderDistance')}")
        result = {"ok": True, "mods_started": False}
        if plan.get("mods") and inst.supports_mods:
            installed = set(self.optimization_status(inst.id).get("installed") or [])
            pack = OPTIMIZATION_FORGE if inst.loader in ("forge", "neoforge") else OPTIMIZATION_PACK
            missing = [slug for slug in pack if slug not in installed]
            if missing:
                r = self.install_optimization(inst.id, missing)
                result["mods_started"] = bool(r.get("ok"))
                result["mods"] = len(missing)
        return result

    def install_optimization(self, instance_id: str, slugs: list) -> dict:
        inst = self._inst(instance_id)
        if not inst.supports_mods:
            return {"error": "Моды оптимизации ставятся в сборки с Fabric, Forge или NeoForge"}
        started = self._runner.run_task(
            "Установка модов оптимизации...",
            lambda: {"instance": inst.id, "result": install_optimization_pack(
                slugs, inst.game_version, inst.loader, inst.game_dir, lambda m: self._on_runner_event("log", m))},
            "optimization_done")
        return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}

    # ================================================================= каталог
    def catalog_search(self, source: str, project_type: str, query: str, sort: str, page: int,
                       instance_id: str = "") -> dict:
        inst = self._instances.get(instance_id)
        game = inst.game_version if inst else ""
        loader = inst.loader if inst and inst.loader != "vanilla" else ""
        src = make_source(source, self._cf_key)
        limit = 20
        if inst and project_type != "modpack":
            self._sync_mods_bg(inst)
        try:
            hits, total = src.search(query, game if project_type != "modpack" else "", loader, project_type,
                                     sort, page * limit, limit)
        except HttpError as exc:
            if source == "curseforge" and exc.status in (401, 403):
                key = self._cf_key
                _log(f"CurseForge {exc}: ключ длина={len(key)} начало={key[:7]} "
                     f"встроенный={key == keys.CURSEFORGE_API_KEY.strip()} код={__file__} pid={os.getpid()}")
                # Повтор напрямую со встроенным ключом — вдруг в памяти остался старый
                try:
                    hits, total = make_source(source, keys.CURSEFORGE_API_KEY.strip()).search(
                        query, game if project_type != "modpack" else "", loader, project_type,
                        sort, page * limit, limit)
                    _log("CurseForge: повтор со встроенным ключом прошёл")
                    index = ModIndex(inst.game_dir) if inst else None
                    have = self._installed_for(inst)
                    return {"total": total, "pages": max(1, -(-total // limit)), "items": [{
                        **asdict(h), "installed": _is_installed(h, index, have)[0],
                        "enabled": _is_installed(h, index, have)[1]} for h in hits]}
                except Exception as exc2:  # noqa: BLE001
                    _log(f"CurseForge: повтор тоже не прошёл: {exc2}")
                raise RuntimeError("CurseForge не принял ключ доступа. Пока ищите моды в Modrinth — "
                                   "это исправится в обновлении лаунчера.") from exc
            raise
        index = ModIndex(inst.game_dir) if inst else None
        have = self._installed_for(inst)
        return {"total": total, "pages": max(1, -(-total // limit)), "items": [{
            **asdict(h), "installed": _is_installed(h, index, have)[0],
                        "enabled": _is_installed(h, index, have)[1]} for h in hits]}

    def _installed_for(self, inst) -> dict:
        """Моды, которые уже стоят в сборке (по файлам), — с любого сайта.
        Не ждём: если результат ещё не готов, считаем в фоне и обновляем список событием mods_synced."""
        empty = {"modrinth": set(), "curseforge": set()}
        if not inst or not inst.supports_mods:
            return empty
        from core import modupdates
        ready = modupdates.cached_keys(inst.game_dir, "")
        if ready is not None:
            return {k: set(v) for k, v in ready.items()}
        running = self.__dict__.setdefault("_keys_running", set())
        if inst.id not in running:
            running.add(inst.id)

            def job():
                try:
                    # без отпечатков CurseForge: их считать в Python долго (500 МБ — десятки секунд и тормоз всего лаунчера).
                    # Моды с CurseForge и так находятся по названию и slug (см. _is_installed).
                    modupdates.installed_keys(inst.game_dir, "", lambda m: _log(m))
                except Exception as exc:  # noqa: BLE001
                    _log(f"[Каталог] не удалось проверить установленные моды: {exc}")
                finally:
                    running.discard(inst.id)
                    if modupdates.keys_ok(inst.game_dir):    # при ошибке сети не перечитываем каталог (иначе — цикл)
                        self._bus.emit("mods_synced", inst.id)   # каталог перечитает список с готовыми отметками
            threading.Thread(target=job, daemon=True).start()
        return empty

    def catalog_install(self, source: str, project_type: str, project_id: str, title: str, instance_id: str) -> dict:
        if project_type == "modpack":
            tmp = Path(tempfile.mkdtemp())
            log = lambda m: self._on_runner_event("log", m)  # noqa: E731

            def job():
                try:
                    if source == "curseforge":
                        from core.packformats import import_curseforge
                        pack = make_source(source, self._cf_key).download_modpack(project_id, tmp, log)
                        inst, problems = import_curseforge(pack, self._instances, self._cf_key, log)
                        self._sync_mods_bg(inst)
                        for p in problems:
                            log(f"[Сборка] {p}")
                        return {**_instance_dict(inst, self._repo.game_dir), "problems": problems}
                    inst = install_mrpack(project_id, title, self._instances, tmp, log=log)
                    self._sync_mods_bg(inst)
                    return _instance_dict(inst, self._repo.game_dir)
                finally:   # скачанный архив сборки больше не нужен — не копим его во временной папке
                    shutil.rmtree(tmp, ignore_errors=True)
            started = self._runner.run_task(f"Установка сборки {title}...", job, "modpack_installed")
            if not started:   # job не запустится — временную папку никто не уберёт
                shutil.rmtree(tmp, ignore_errors=True)
            return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}
        inst = self._inst(instance_id)
        if project_type == "mod" and not inst.supports_mods:
            return {"error": "В Vanilla-сборку моды не ставятся — создайте сборку с Fabric, Forge или NeoForge"}
        ModIndex(inst.game_dir).unmark_removed(f"{source}:{project_id}")   # явная установка снимает «удалено сами»
        try:
            files = make_source(source, self._cf_key).install(
                project_id, inst.game_version, inst.loader if inst.supports_mods else "fabric", inst.game_dir,
                project_type, title)
            self._on_runner_event("log", f"[Каталог] {title}: {', '.join(files) or 'уже установлен'}")
            return {"files": files}
        except DistributionBlocked as exc:
            return {"error": str(exc), "url": exc.url}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def catalog_set_enabled(self, source: str, project_id: str, instance_id: str, enabled: bool) -> dict:
        """Галочка в каталоге: выключить (файл остаётся в сборке, игра его не загружает) или включить обратно."""
        inst = self._inst(instance_id)
        try:
            state = ModIndex(inst.game_dir).set_enabled(source, project_id, bool(enabled))
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except OSError:
            return {"error": "Файл занят — закройте игру и попробуйте снова"}
        return {"ok": True, "enabled": state}

    def catalog_uninstall(self, source: str, project_id: str, instance_id: str) -> dict:
        """Убрать из сборки мод / ресурспак / шейдер, установленный из каталога."""
        inst = self._inst(instance_id)
        if self._runner.state == "running" and self._running_instance == inst.id:
            return {"error": "Закройте игру — файлы сейчас заняты"}
        index = ModIndex(inst.game_dir)
        key = f"{source}:{project_id}"
        entry = index.data.get(key)
        try:
            removed = index.remove(source, project_id)
        except OSError as exc:
            return {"error": f"Не удалось удалить файл: {exc}"}
        if isinstance(entry, dict) and entry.get("file"):   # ставили из каталога — запоминаем, чтобы не вернулось само
            index.mark_removed(key, "file:" + Path(entry["file"]).name.removesuffix(".disabled"))
        self._on_runner_event("log", f"[Каталог] Удалено: {', '.join(removed) or 'файл уже был удалён'}")
        return {"ok": True, "files": removed}

    # ================================================================= запуск
    def _prepare_auth(self, acc: Account) -> AuthInfo:
        if acc.kind == "offline":
            return AuthInfo.offline(acc.username)
        if acc.kind == "elyby":
            elyby.ensure_valid(acc)
            self._accounts.save()
            if not _host_reachable("account.ely.by", 443):
                # authlib-injector при старте скачивает данные с Ely.by: без связи игра закрывается через ~20 секунд без объяснений
                raise AuthError("Нет связи с Ely.by (account.ely.by не отвечает). Игра с этим аккаунтом без него не запустится: "
                                "проверьте интернет и VPN или попробуйте через минуту. Можно временно войти оффлайн-аккаунтом.")
            jar = ensure_authlib_injector(self._repo.game_dir)
            return AuthInfo(acc.username, acc.uuid, acc.token(), "mojang", [javaagent_arg(jar)])
        microsoft.refresh(self._cfg.ms_client_id, acc)
        self._accounts.save()
        return AuthInfo(acc.username, acc.uuid, acc.token(), "msa")

    def play(self, instance_id: str, server: str = "", force: bool = False) -> dict:
        acc = self._accounts.active
        if acc is None:
            return {"error": "Войдите в аккаунт"}
        inst = self._instances.get(instance_id)
        if inst is None:
            return {"error": "Создайте или выберите сборку"}
        ram = inst.ram_gb or self._auto_ram(inst)
        total = total_ram_gb() or 0
        if total and ram > max(2, total - 3):   # больше, чем есть ОЗУ: система уходит в подкачку и всё зависает
            safe_ram = max(2, total - 3)
            self._on_runner_event("log", f"[Память] {ram} ГБ — слишком много для ПК с {total} ГБ: "
                                         f"выделяю {safe_ram} ГБ, чтобы система не зависала")
            ram = safe_ram
        target = LaunchTarget(inst.version_id, inst.game_dir, ram, inst.jvm_args, inst.name, compat=inst.compat)
        if inst.compat:
            self._on_runner_event("log", "[Совместимость] Режим совместимости: игра запускается без оптимизаций лаунчера")
        if self._runner.busy:
            return {"error": "Уже идёт другая операция"}
        log = lambda m: self._on_runner_event("log", m)  # noqa: E731
        t_play = time.monotonic()
        try:   # известные конфликты — выключаем лишний мод сами, до проверки
            from core.conflicts import fix_conflicts
            fixed = fix_conflicts(inst.mods_dir, log)
            if fixed:
                self._bus.emit("conflicts_fixed", fixed)
        except Exception as exc:  # noqa: BLE001
            log(f"[Конфликт] {exc}")
        if not force and inst.loader in ("fabric", "quilt", "forge", "neoforge"):
            try:   # моды не под тот загрузчик / версию, нет зависимостей, дубли — говорим ДО запуска
                from core.modcheck import check_mods
                issues = check_mods(inst.mods_dir, inst.loader, inst.game_version)
            except Exception as exc:  # noqa: BLE001  проверка не должна мешать играть
                log(f"[Проверка модов] {exc}")
                issues = []
            if issues:
                return {"modcheck": issues, "instance": inst.id}
        t_checks = time.monotonic()
        # комната — только если игрок нажал «Открыть мир для друзей»; иначе e4mc не выдаёт адрес при «Открыть для сети»
        # «Открыть мир для друзей» нажали недавно — этот запуск комната. Через 10 минут уже нет: иначе обычное
        # «Играть» (например, после отменённой проверки модов) вдруг создало бы комнату, а игрок хотел Radmin
        pend_id, pend_at = self._room_pending
        room = pend_id == inst.id and time.time() - pend_at < 600
        self._room_pending = ("", 0.0)
        self._room_mode = room
        self._room_code, self._room_address = "", ""
        try:   # друзья без лицензии заходят, только если в мире выключен «Online Mode» — делаем это сами (и для Radmin тоже)
            from core import lanworld
            if lanworld.has_mod(inst.game_dir):
                lanworld.disable_online_mode(inst.game_dir)
            if lanworld.set_e4mc_host(inst.game_dir, room) and not room:
                log("[Комната] Обычный запуск: «Открыть для сети» работает как всегда, адрес e4mc не создаётся")
        except Exception as exc:  # noqa: BLE001
            log(f"[Комната] {exc}")
        self._running_instance = inst.id
        self._last_ram = ram              # сколько реально выделено: окно «Игра вылетела» предлагает «больше» от этого числа
        self._running_account = acc.id    # время игры — тому, кто запустил, даже если аккаунт сменили во время игры
        self._cfg.selected_instance = inst.id
        self._save()
        def prepare():
            if self._cfg.skins_in_game:
                try:
                    from core.skinserver import ensure_csl
                    ensure_csl(inst.game_dir, inst.game_version, inst.loader, lambda m: self._on_runner_event("log", m))
                except Exception as exc:  # noqa: BLE001  скины — не повод не запускать игру
                    self._on_runner_event("log", f"[Скины] {exc}")
            return self._prepare_auth(acc)

        # заголовок — до запуска, иначе строки подготовки (скины, аккаунт) оказывались над ним
        self._on_runner_event("log", f"===== {inst.name} ({display_name(inst.version_id)}) — {acc.username} [{acc.label}] =====")
        timing_log(f"{inst.version_id}: до запуска подготовки {time.monotonic() - t_play:.2f} с "
                   f"(проверка модов и настройка {t_checks - t_play:.2f} с)")
        server = server.strip()
        old_bridge = getattr(self, "_dns_bridge", None)
        if old_bridge:
            old_bridge.close()
        self._dns_bridge = None
        if server:   # провайдер не находит адрес (например, e4mc.link) — находим сами и идём через локальный мост
            try:
                from core import dnsrescue
                server, self._dns_bridge = dnsrescue.rescue(server, log)
            except Exception as exc:  # noqa: BLE001  не вышло — запускаем как есть, игра сама скажет
                log(f"[DNS] {exc}")
        if not self._runner.launch(target, prepare, server):
            if self._dns_bridge:
                self._dns_bridge.close()
                self._dns_bridge = None
            return {"error": "Уже идёт другая операция"}
        return {"ok": True}

    def _auto_ram(self, inst) -> int:
        """Память для сборки без своей настройки: общая из настроек, но для больших модпаков — больше.
        Мало памяти → частая сборка мусора → фризы и просадки FPS."""
        ram = self._cfg.ram_gb
        mods = inst.mods_dir
        count = sum(1 for p in mods.iterdir() if p.suffix == ".jar") if mods.is_dir() else 0
        want = 8 if count >= 120 else 6 if count >= 40 else 0
        total = total_ram_gb() or 0
        if want > ram and total >= want + 4:  # системе оставляем минимум 4 ГБ
            self._on_runner_event("log", f"[Память] {count} модов: выделяю {want} ГБ вместо {ram} ГБ "
                                         f"(свою память можно задать в настройках сборки)")
            return want
        return ram

    def install_instance(self, instance_id: str) -> dict:
        ok = self._runner.install(self._inst(instance_id).version_id)
        return {"ok": ok} if ok else {"error": "Уже идёт другая операция"}

    def cancel(self):
        self._runner.cancel()

    def kill_game(self):
        self._runner.kill()

    # ================================================================= друзья и комнаты
    def friends(self) -> list:
        return [asdict(f) for f in self._friends.friends]

    def friend_add(self, name: str, address: str = "", note: str = "") -> list:
        if name.strip():
            self._friends.upsert(name.strip(), address.strip(), note.strip())
        return self.friends()

    def friend_add_code(self, code: str) -> dict:
        try:
            name, address = parse_friend_code(code)
        except Exception:  # noqa: BLE001
            return {"error": "Неверный код друга"}
        self._friends.upsert(name, address)
        return {"friends": self.friends(), "name": name}

    def friend_remove(self, name: str) -> list:
        for f in list(self._friends.friends):
            if f.name == name:
                self._friends.remove(f)
        return self.friends()

    def my_friend_code(self) -> str:
        acc = self._accounts.active
        return make_friend_code(acc.username if acc else "Player", self._room_address)

    def room_address(self) -> str:
        return self._room_address

    def prepare_room(self, instance_id: str, tunnel: str) -> dict:
        inst = self._inst(instance_id)
        if inst.loader not in ("fabric", "forge", "neoforge", "quilt"):
            return {"error": "Для комнаты нужна сборка с модами (Fabric, Forge или NeoForge) — в чистую игру мод не поставить"}
        if tunnel not in TUNNEL_MODS:
            tunnel = "e4mc+lan"
        self._cfg.tunnel_mod = tunnel
        self._save()
        self._room_pending = (inst.id, time.time())   # следующий запуск этой сборки (в ближайшие 10 мин) — как комната
        started = self._runner.run_task(
            "Установка мода комнаты...",
            lambda: {"instance": inst.id, "tunnel": tunnel,
                     "result": install_tunnel(tunnel, inst.game_version, inst.loader, inst.game_dir,
                                              lambda m: self._on_runner_event("log", m))},
            "room_ready")
        return {"ok": started} if started else {"error": "Дождитесь окончания текущей операции"}

    # ================================================================= настройки: Java и прочее
    def find_java(self) -> list:
        return [{"path": j.path, "major": j.major} for j in find_java_installs(self._repo.game_dir)]

    def java_version(self, path: str) -> int:
        return java_major_version(path) or 0

    def pick_java(self) -> str:
        return self._dialog("open") or ""

    def pick_folder(self) -> str:
        return self._dialog("folder") or ""

    def create_shortcut(self) -> dict:
        self._cfg.shortcut_offered = True
        self._save()
        try:
            link = create_desktop_shortcut()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"path": str(link)} if link else {"error": "Не удалось создать ярлык"}

    def dismiss_shortcut(self):
        self._cfg.shortcut_offered = True
        self._save()
