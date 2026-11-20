"""
Сборки (instances): у каждой своя папка игры — свои моды, конфиги, миры, ресурспаки.
Общие тяжёлые файлы (versions/, libraries/, assets/) лежат в data_dir и не дублируются.

instances/<id>/instance.json   — настройки сборки
instances/<id>/minecraft/      — папка игры (--gameDir)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .utils import INSTANCES_DIR

PACK_EXT = ".vaultpack"
OLD_PACK_EXT = ".neonpack"  # прежний формат тоже открывается
# Что попадает в экспорт сборки (миры и кэш — нет: они большие и личные)
EXPORT_DIRS = ("mods", "config", "resourcepacks", "shaderpacks")
EXPORT_FILES = ("options.txt", "servers.dat", ".neon_mods.json")

COLORS = ["#22c55e", "#3b82f6", "#a855f7", "#f97316", "#ec4899", "#14b8a6", "#eab308", "#ef4444"]


@dataclass
class Instance:
    id: str
    name: str
    version_id: str            # id в versions/ (для Fabric: fabric-loader-X-Y)
    game_version: str
    loader: str = "vanilla"    # vanilla | fabric
    color: str = "#22c55e"
    created_at: float = field(default_factory=time.time)
    last_played: float = 0.0
    play_seconds: int = 0
    ram_gb: int = 0            # 0 = как в общих настройках
    jvm_args: str = ""
    notes: str = ""
    compat: bool = False       # «режим совместимости»: запуск без оптимизаций лаунчера (как в обычных лаунчерах)

    @property
    def root(self) -> Path:
        return INSTANCES_DIR / self.id

    @property
    def game_dir(self) -> Path:
        return self.root / "minecraft"

    @property
    def mods_dir(self) -> Path:
        return self.game_dir / "mods"

    @property
    def supports_mods(self) -> bool:
        return self.loader != "vanilla"


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    return slug or "instance"


class InstanceManager:
    def __init__(self, root: Path = INSTANCES_DIR):
        self.root = root
        self._cache: dict[str, Instance] | None = None

    # ---------------------------------------------------------------- чтение / запись
    def list(self) -> list[Instance]:
        if self._cache is None:
            self._cache = {}
            if self.root.is_dir():
                known = Instance.__dataclass_fields__
                for d in self.root.iterdir():
                    meta = d / "instance.json"
                    if meta.exists():
                        try:
                            data = json.loads(meta.read_text(encoding="utf-8"))
                            inst = Instance(**{k: v for k, v in data.items() if k in known})
                            inst.id = d.name
                            self._cache[inst.id] = inst
                        except (OSError, json.JSONDecodeError, TypeError):
                            continue
        return sorted(self._cache.values(), key=lambda i: (i.last_played, i.created_at), reverse=True)

    def get(self, instance_id: str) -> Instance | None:
        self.list()
        return self._cache.get(instance_id) if self._cache else None

    def save(self, inst: Instance) -> None:
        inst.root.mkdir(parents=True, exist_ok=True)
        tmp = inst.root / f"instance.json.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(asdict(inst), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, inst.root / "instance.json")
        if self._cache is not None:
            self._cache[inst.id] = inst

    def _unique_id(self, name: str) -> str:
        base = slugify(name)
        candidate, n = base, 2
        while (self.root / candidate).exists():
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    # ---------------------------------------------------------------- действия
    def create(self, name: str, version_id: str, game_version: str, loader: str) -> Instance:
        name = name.strip() or game_version
        inst = Instance(id=self._unique_id(name), name=name, version_id=version_id, game_version=game_version,
                        loader=loader, color=COLORS[len(self.list()) % len(COLORS)])
        for sub in ("mods", "saves", "resourcepacks", "config"):
            (inst.game_dir / sub).mkdir(parents=True, exist_ok=True)
        self.save(inst)
        return inst

    def delete(self, inst: Instance) -> None:
        shutil.rmtree(inst.root, ignore_errors=True)
        if self._cache:
            self._cache.pop(inst.id, None)

    def duplicate(self, inst: Instance) -> Instance:
        new_id = self._unique_id(inst.name + " копия")
        src_root = str(inst.root)

        def skip(folder: str, names: list[str]) -> set[str]:
            # копии миров, корзина и точки сборки остаются у оригинала: раньше они копировались целиком,
            # и «Копия сборки» с десятком копий миров занимала гигабайты и делалась минутами
            own = {"backups", "trash", "snapshots", ".vaultmc-sync"} if os.path.normpath(folder) == os.path.normpath(src_root) else set()
            return {n for n in names if n in ("logs", "crash-reports") or n in own}
        shutil.copytree(inst.root, self.root / new_id, ignore=skip)
        copy = Instance(**{**asdict(inst), "id": new_id, "name": inst.name + " (копия)",
                           "created_at": time.time(), "last_played": 0.0, "play_seconds": 0})
        self.save(copy)
        return copy

    def backup_worlds(self, inst: Instance) -> Path | None:
        """Архив всех миров сборки в instances/<id>/backups/. Без session.lock и кэша модов (кэш Distant Horizons —
        это гигабайты, мод пересоздаёт его сам): раньше копия всех миров тащила его целиком."""
        from .worlds import world_files
        saves = inst.game_dir / "saves"
        if not saves.is_dir() or not any(saves.iterdir()):
            return None
        backups = inst.root / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp, n = time.strftime("%Y-%m-%d_%H-%M-%S"), 1
        target = backups / f"worlds-{stamp}.zip"
        while target.exists():                   # две копии в одну секунду: вторая не затирает первую
            n += 1
            target = backups / f"worlds-{stamp}-{n}.zip"
        tmp = target.with_suffix(".part")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
                for entry in sorted(saves.iterdir()):
                    if entry.is_dir():
                        for f in world_files(entry):
                            zf.write(f, f"{entry.name}/{f.relative_to(entry).as_posix()}")
                    elif entry.is_file():
                        zf.write(entry, entry.name)
            os.replace(tmp, target)              # недописанная копия (выключили ПК) не выглядит как целая
        finally:
            tmp.unlink(missing_ok=True)
        return target

    # ---------------------------------------------------------------- обмен сборками
    def export_pack(self, inst: Instance, dest: Path) -> Path:
        """Один файл .vaultpack: настройки + моды + конфиги. Можно отправить другу."""
        dest = dest.with_suffix(PACK_EXT)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            meta = asdict(inst)
            for key in ("id", "last_played", "play_seconds", "created_at"):
                meta.pop(key, None)
            zf.writestr("instance.json", json.dumps(meta, indent=2, ensure_ascii=False))
            for sub in EXPORT_DIRS:
                folder = inst.game_dir / sub
                if folder.is_dir():
                    for f in folder.rglob("*"):
                        if f.is_file():
                            zf.write(f, f"minecraft/{f.relative_to(inst.game_dir).as_posix()}")
            for name in EXPORT_FILES:
                f = inst.game_dir / name
                if f.exists():
                    zf.write(f, f"minecraft/{name}")
        return dest

    def import_pack(self, pack: Path) -> Instance:
        with zipfile.ZipFile(pack) as zf:
            meta = json.loads(zf.read("instance.json").decode("utf-8"))
            known = Instance.__dataclass_fields__
            inst = Instance(**{k: v for k, v in meta.items() if k in known and k != "id"},
                            id=self._unique_id(meta.get("name", pack.stem)))
            for member in zf.namelist():
                if not member.startswith("minecraft/") or member.endswith("/"):
                    continue
                target = (inst.root / member).resolve()
                if inst.root.resolve() not in target.parents:  # защита от путей вида ../../
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
        (inst.game_dir / "saves").mkdir(parents=True, exist_ok=True)
        self.save(inst)
        return inst
