"""
Версии игры: полный список с серверов Mojang (релизы, снапшоты, бета, альфа),
версии Fabric и уже установленные локально (включая Forge/OptiFine и т.п.).
Списки кэшируются на диск — без интернета показывается последний сохранённый.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .net import get_json
from .utils import LAUNCHER_HOME

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2"
MANIFEST_CACHE = LAUNCHER_HOME / "cache" / "version_manifest_v2.json"
FABRIC_CACHE = LAUNCHER_HOME / "cache" / "fabric.json"
FORGE_CACHE = LAUNCHER_HOME / "cache" / "forge.json"

TYPE_LABELS = {
    "release": "Релиз",
    "snapshot": "Снапшот",
    "old_beta": "Бета",
    "old_alpha": "Альфа",
    "fabric": "Fabric",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "local": "Локальная",
}

_FABRIC_RE = re.compile(r"^fabric-loader-([^-]+)-(.+)$")


def fabric_id(loader: str, game: str) -> str:
    return f"fabric-loader-{loader}-{game}"


def parse_fabric_id(version_id: str) -> tuple[str, str] | None:
    """'fabric-loader-0.16.5-1.21.1' -> ('0.16.5', '1.21.1')."""
    match = _FABRIC_RE.match(version_id)
    return (match.group(1), match.group(2)) if match else None


@dataclass
class RemoteVersion:
    id: str
    type: str
    url: str
    release_time: str
    sha1: str = ""


@dataclass
class VersionItem:
    """Строка в списке версий интерфейса."""
    id: str            # id для запуска (папка в versions/)
    game_version: str  # версия Minecraft
    type: str          # ключ из TYPE_LABELS
    release_time: str
    installed: bool


def display_name(version_id: str) -> str:
    """Короткое имя для интерфейса: 'fabric-loader-0.16.5-1.21.1' -> 'Fabric 1.21.1'."""
    parsed = parse_fabric_id(version_id)
    if parsed:
        return f"Fabric {parsed[1]}"
    from .forge import parse_loader_id
    forge = parse_loader_id(version_id)
    if forge:
        return f"{'Forge' if forge[0] == 'forge' else 'NeoForge'} {forge[1]}"
    return version_id


class VersionRepository:
    def __init__(self, game_dir: Path):
        self.game_dir = game_dir
        self.remote: list[RemoteVersion] = []
        self._by_id: dict[str, RemoteVersion] = {}
        self.latest: dict[str, str] = {}
        self.fabric_games: set[str] = set()
        self.fabric_loader: str | None = None
        self.forge_meta: dict = {"forge": {}, "neoforge": {}}
        self.offline = False

    @property
    def versions_dir(self) -> Path:
        return self.game_dir / "versions"

    # ---------------------------------------------------------------- загрузка списков
    def refresh_remote(self) -> bool:
        """Обновляет списки с сервера (или из кэша). Вызывать из фонового потока. True — онлайн."""
        online = True
        try:
            manifest = get_json(MANIFEST_URL)
            MANIFEST_CACHE.parent.mkdir(parents=True, exist_ok=True)
            MANIFEST_CACHE.write_text(json.dumps(manifest), encoding="utf-8")
        except Exception:  # noqa: BLE001
            online = False
            manifest = self._read_cache(MANIFEST_CACHE) or {"versions": [], "latest": {}}

        try:
            games = get_json(f"{FABRIC_META}/versions/game")
            loaders = get_json(f"{FABRIC_META}/versions/loader")
            fabric = {"games": [g["version"] for g in games],
                      "loader": next((l["version"] for l in loaders if l.get("stable")), loaders[0]["version"])}
            FABRIC_CACHE.write_text(json.dumps(fabric), encoding="utf-8")
        except Exception:  # noqa: BLE001
            online = False
            fabric = self._read_cache(FABRIC_CACHE) or {"games": [], "loader": None}

        from .forge import fetch_meta
        forge_meta = fetch_meta(FORGE_CACHE)

        remote = [RemoteVersion(v["id"], v["type"], v["url"], v.get("releaseTime", ""), v.get("sha1", ""))
                  for v in manifest.get("versions", [])]
        # присваиваем в конце — интерфейс читает эти поля из главного потока
        self._by_id = {v.id: v for v in remote}
        self.remote = remote
        self.latest = manifest.get("latest", {})
        self.fabric_games = set(fabric.get("games", []))
        self.fabric_loader = fabric.get("loader")
        self.forge_meta = forge_meta
        self.offline = not online
        return online

    @staticmethod
    def _read_cache(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # ---------------------------------------------------------------- локальные версии
    def installed_ids(self) -> list[str]:
        if not self.versions_dir.is_dir():
            return []
        return sorted((d.name for d in self.versions_dir.iterdir() if (d / f"{d.name}.json").exists()),
                      reverse=True)

    def is_installed(self, version_id: str) -> bool:
        return (self.versions_dir / version_id / f"{version_id}.json").exists()

    def remote_entry(self, version_id: str) -> RemoteVersion | None:
        return self._by_id.get(version_id)

    def delete_version(self, version_id: str) -> None:
        shutil.rmtree(self.versions_dir / version_id, ignore_errors=True)

    def type_of(self, version_id: str) -> str:
        if parse_fabric_id(version_id):
            return "fabric"
        from .forge import parse_loader_id
        forge = parse_loader_id(version_id)
        if forge:
            return forge[0]
        entry = self._by_id.get(version_id)
        return entry.type if entry else "local"

    def game_version_of(self, version_id: str) -> str:
        parsed = parse_fabric_id(version_id)
        if parsed:
            return parsed[1]
        from .forge import parse_loader_id
        forge = parse_loader_id(version_id)
        if forge:
            return forge[1]
        if self.is_installed(version_id):
            try:
                data = json.loads((self.versions_dir / version_id / f"{version_id}.json").read_text(encoding="utf-8"))
                return data.get("inheritsFrom", version_id)
            except (OSError, json.JSONDecodeError):
                pass
        return version_id

    # ---------------------------------------------------------------- список для интерфейса
    def items(self, loader: str, types: set[str], query: str = "", only_installed: bool = False) -> list[VersionItem]:
        installed = set(self.installed_ids())
        query = query.strip().lower()
        result: list[VersionItem] = []

        if loader == "fabric":
            installed_fabric = {}
            for vid in installed:
                parsed = parse_fabric_id(vid)
                if parsed:
                    installed_fabric.setdefault(parsed[1], vid)
            for rv in self.remote:
                if rv.id not in self.fabric_games or rv.type not in types:
                    continue
                vid = installed_fabric.get(rv.id) or (fabric_id(self.fabric_loader, rv.id) if self.fabric_loader else None)
                if vid:
                    result.append(VersionItem(vid, rv.id, "fabric", rv.release_time, vid in installed))
            known = {i.id for i in result}
            for game, vid in installed_fabric.items():  # установленные Fabric без списка с сервера
                if vid not in known:
                    result.append(VersionItem(vid, game, "fabric", "", True))
        elif loader in ("forge", "neoforge"):
            from .forge import loader_id, parse_loader_id
            builds = self.forge_meta.get(loader, {})
            installed_loader = {}
            for vid in installed:
                parsed = parse_loader_id(vid)
                if parsed and parsed[0] == loader:
                    installed_loader.setdefault(parsed[1], vid)
            for rv in self.remote:
                if rv.type not in types or (rv.id not in builds and rv.id not in installed_loader):
                    continue
                vid = installed_loader.get(rv.id) or loader_id(loader, rv.id, builds[rv.id][0])
                result.append(VersionItem(vid, rv.id, loader, rv.release_time, vid in installed))
            known = {i.game_version for i in result}
            for game, vid in installed_loader.items():
                if game not in known:
                    result.append(VersionItem(vid, game, loader, "", True))
        else:
            for rv in self.remote:
                if rv.type in types:
                    result.append(VersionItem(rv.id, rv.id, rv.type, rv.release_time, rv.id in installed))
            from .forge import parse_loader_id
            for vid in installed:  # OptiFine, свои сборки и т.п.
                if vid not in self._by_id and not parse_fabric_id(vid) and not parse_loader_id(vid):
                    result.insert(0, VersionItem(vid, self.game_version_of(vid), "local", "", True))

        if query:
            result = [i for i in result if query in i.id.lower()]
        if only_installed:
            result = [i for i in result if i.installed]
        return result

    # ---------------------------------------------------------------- version.json
    def load_version_json(self, version_id: str) -> dict:
        """version.json с учётом inheritsFrom (Fabric/Forge наследуют ванильную версию)."""
        path = self.versions_dir / version_id / f"{version_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        parent_id = data.get("inheritsFrom")
        if parent_id:
            base = self.load_version_json(parent_id)
            merged = self._merge(base, data)
            own_jar = self.versions_dir / version_id / f"{version_id}.jar"
            if "jar" not in data and not own_jar.exists():   # Forge кладёт свою копию jar игры
                merged["jar"] = base.get("jar", parent_id)
            return merged
        return data

    @staticmethod
    def _merge(base: dict, child: dict) -> dict:
        merged = dict(base)
        merged.pop("jar", None)
        for key, value in child.items():
            if key == "libraries":
                merged["libraries"] = value + base.get("libraries", [])
            elif key == "arguments":
                args = {k: list(v) for k, v in base.get("arguments", {}).items()}
                for k, v in value.items():
                    args[k] = args.get(k, []) + v
                merged["arguments"] = args
            else:
                merged[key] = value
        return merged

    def loader_builds(self, loader: str, game: str) -> list[str]:
        """Сборки Forge/NeoForge для версии игры (рекомендованная — первой)."""
        return list(self.forge_meta.get(loader, {}).get(game, []))
