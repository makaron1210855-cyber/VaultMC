"""Глобальные настройки лаунчера (~/.vaultmc/config.json). Настройки сборок — в instance.json."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .utils import DEFAULT_DATA_DIR, LAUNCHER_HOME, OLD_LAUNCHER_HOME

RAM_MIN, RAM_MAX = 2, 16


@dataclass
class LauncherConfig:
    # Выбор
    selected_instance: str = ""
    # Фильтры списка версий (окно «Новая сборка»)
    version_loader: str = "vanilla"          # vanilla | fabric
    show_release: bool = True
    show_snapshot: bool = False
    show_beta: bool = False
    show_alpha: bool = False
    # Игра (значения по умолчанию для всех сборок)
    ram_gb: int = 4
    data_dir: str = field(default_factory=lambda: str(DEFAULT_DATA_DIR))
    java_path: str = ""
    java_auto: bool = True
    window_width: int = 1280
    window_height: int = 720
    fullscreen: bool = False
    server_address: str = ""
    # Производительность игры
    optimize_jvm: bool = True                # флаги G1GC
    high_priority: bool = True               # приоритет процесса «выше обычного» (Windows)
    discrete_gpu: bool = True                # просить Windows использовать мощную видеокарту
    extra_jvm_args: str = ""
    # Каталоги модов
    mod_source: str = "modrinth"             # modrinth | curseforge
    curseforge_api_key: str = ""
    # Вход Microsoft (нужен свой Client ID приложения Azure — см. README)
    ms_client_id: str = ""
    # Совместная игра
    tunnel_mod: str = "e4mc+lan"             # e4mc+lan (e4mc + LAN World Plug-n-Play) | e4all | e4mc
    tunnel_migrated: bool = False            # старый выбор e4all один раз переводится на e4mc+lan
    # Лаунчер
    accent: str = "Алмазный"       # цвет интерфейса
    hide_launcher: bool = False          # больше не используется (лаунчер в игре не прячется)
    skins_in_game: bool = True           # CustomSkinLoader + сервер скинов в сборках с модами
    show_online: bool = True             # друзья видят, что я в сети и во что играю
    download_threads: int = 32
    demo_mode: bool = False
    shortcut_offered: bool = False
    # Состояние интерфейса: панель свёрнута, последняя вкладка, фильтры каталога, размер окна...
    ui: dict = field(default_factory=dict)

    def version_types(self) -> set[str]:
        return {t for t, on in (("release", self.show_release), ("snapshot", self.show_snapshot),
                                ("old_beta", self.show_beta), ("old_alpha", self.show_alpha)) if on}


class ConfigManager:
    _lock = threading.Lock()  # интерфейс может сохранять настройки из нескольких потоков одновременно

    def __init__(self, path: Path = LAUNCHER_HOME / "config.json"):
        self.path = path
        self.config = self.load()

    def load(self) -> LauncherConfig:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in LauncherConfig.__dataclass_fields__}
                cfg = LauncherConfig(**known)
                cfg.ram_gb = max(RAM_MIN, min(RAM_MAX, int(cfg.ram_gb)))
                # после переезда ~/.neon_launcher -> ~/.vaultmc исправляем сохранённые пути
                old = str(OLD_LAUNCHER_HOME)
                if cfg.data_dir.startswith(old) and not Path(cfg.data_dir).exists():
                    cfg.data_dir = str(LAUNCHER_HOME) + cfg.data_dir[len(old):]
                return cfg
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                # файл повреждён: откладываем копию, иначе первое же сохранение молча сотрёт все настройки
                # (папку файлов игры, путь к Java, память и т. д.)
                try:
                    self.path.replace(self.path.with_name(f"config.broken-{int(time.time())}.json"))
                except OSError:
                    pass
        return LauncherConfig()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(asdict(self.config), indent=4, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
