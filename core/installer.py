"""
Установщик версий: скачивает version.json, client.jar, библиотеки, natives и ассеты
с официальных серверов Mojang; для Fabric — профиль и библиотеки с meta.fabricmc.net.
Файлы качаются параллельно, уже скачанные пропускаются (повторный запуск = проверка/починка).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .net import Cancelled, bytes_downloaded, download, format_speed, get_json, is_valid
from .rules import rules_allow
from .utils import default_minecraft_dir, maven_to_path, mojang_os_name
from .versions import FABRIC_META, VersionRepository, fabric_id, parse_fabric_id

RESOURCES_URL = "https://resources.download.minecraft.net"
MOJANG_LIBRARIES = "https://libraries.minecraft.net/"


class InstallError(Exception):
    pass


@dataclass
class DownloadTask:
    url: str
    dest: Path
    sha1: str | None = None
    size: int | None = None


class Installer:
    def __init__(self, repo: VersionRepository, threads: int,
                 progress: Callable[[float, str], None], log: Callable[[str], None],
                 cancel: threading.Event, java: Callable[[int], str] | None = None):
        self.repo = repo
        self.java = java          # путь к java нужной версии (для процессоров Forge)
        self.threads = max(1, threads)
        self.progress = progress
        self.log = log
        self.cancel = cancel
        # Если есть официальный .minecraft — берём уже скачанные файлы оттуда (копия вместо загрузки)
        official = default_minecraft_dir()
        self.mirrors = [official] if official.is_dir() and official.resolve() != repo.game_dir.resolve() else []

    @property
    def game_dir(self) -> Path:
        return self.repo.game_dir

    # ---------------------------------------------------------------- вход
    def install(self, version_id: str) -> None:
        from . import forge
        parsed = parse_fabric_id(version_id)
        loader = forge.parse_loader_id(version_id)
        if parsed:
            self.install_fabric(*parsed)
        elif loader and not self.repo.is_installed(version_id):
            forge.install(self, *loader, java=self._java_for)
        else:
            self.install_vanilla(version_id)
        self.progress(1.0, f"{version_id} установлена")

    def _java_for(self, game: str) -> str:
        """Java той же версии, что нужна самой игре (процессоры Forge под 1.20.5+ требуют Java 21)."""
        try:
            data = json.loads((self.repo.versions_dir / game / f"{game}.json").read_text(encoding="utf-8"))
            major = int(data.get("javaVersion", {}).get("majorVersion", 8))
        except (OSError, ValueError):
            major = 17
        if self.java is None:
            found = shutil.which("java")
            if not found:
                raise InstallError("Для установки Forge нужна Java")
            return found
        return self.java(major)

    # ---------------------------------------------------------------- vanilla
    def install_vanilla(self, version_id: str) -> None:
        vdir = self.repo.versions_dir / version_id
        json_path = vdir / f"{version_id}.json"
        entry = self.repo.remote_entry(version_id)

        self.progress(0.02, f"{version_id}: описание версии...")
        if entry:
            download(entry.url, json_path, sha1=entry.sha1 or None, cancel=self.cancel)
        elif not json_path.exists():
            raise InstallError(f"Версия {version_id} не найдена (нет интернета или неверный id)")
        data = json.loads(json_path.read_text(encoding="utf-8"))

        if data.get("inheritsFrom"):  # локальные Forge/OptiFine-профили
            self.install_vanilla(data["inheritsFrom"])

        tasks: list[DownloadTask] = []
        client = data.get("downloads", {}).get("client")
        if client:
            tasks.append(DownloadTask(client["url"], vdir / f"{version_id}.jar", client.get("sha1"), client.get("size")))
        tasks += self._library_tasks(data)
        tasks += self._asset_tasks(data)
        self.run_tasks(tasks, version_id)
        self._prepare_legacy_assets(data)

    def _library_tasks(self, data: dict) -> list[DownloadTask]:
        libs_dir = self.game_dir / "libraries"
        tasks = []
        for lib in data.get("libraries", []):
            if not rules_allow(lib.get("rules")):
                continue
            downloads = lib.get("downloads")
            if downloads is None:  # формат Fabric/Maven: name + url репозитория
                rel = maven_to_path(lib["name"])
                base = lib.get("url", MOJANG_LIBRARIES).rstrip("/") + "/"
                tasks.append(DownloadTask(base + rel, libs_dir / rel, lib.get("sha1"), lib.get("size")))
                continue
            artifact = downloads.get("artifact")
            if artifact and artifact.get("url"):
                tasks.append(DownloadTask(artifact["url"], libs_dir / artifact["path"],
                                          artifact.get("sha1"), artifact.get("size")))
            natives = lib.get("natives", {}).get(mojang_os_name())
            if natives:
                classifier = downloads.get("classifiers", {}).get(natives.replace("${arch}", "64"))
                if classifier:
                    tasks.append(DownloadTask(classifier["url"], libs_dir / classifier["path"],
                                              classifier.get("sha1"), classifier.get("size")))
        return tasks

    def _asset_tasks(self, data: dict) -> list[DownloadTask]:
        index_info = data.get("assetIndex")
        if not index_info:
            return []
        index_path = self.game_dir / "assets" / "indexes" / f"{index_info['id']}.json"
        self.progress(0.04, "Индекс ассетов...")
        download(index_info["url"], index_path, index_info.get("sha1"), index_info.get("size"), cancel=self.cancel)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        objects_dir = self.game_dir / "assets" / "objects"
        return [DownloadTask(f"{RESOURCES_URL}/{o['hash'][:2]}/{o['hash']}", objects_dir / o["hash"][:2] / o["hash"],
                             o["hash"], o.get("size"))
                for o in index.get("objects", {}).values()]

    def _prepare_legacy_assets(self, data: dict) -> None:
        """Старые версии (до 1.7) читают ассеты по именам, а не по хэшам — копируем."""
        index_info = data.get("assetIndex")
        if not index_info:
            return
        index_path = self.game_dir / "assets" / "indexes" / f"{index_info['id']}.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if index.get("map_to_resources"):
            target_root = self.game_dir / "resources"
        elif index.get("virtual"):
            target_root = self.game_dir / "assets" / "virtual" / index_info["id"]
        else:
            return
        for name, obj in index.get("objects", {}).items():
            src = self.game_dir / "assets" / "objects" / obj["hash"][:2] / obj["hash"]
            dst = target_root / name
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    # ---------------------------------------------------------------- fabric
    def install_fabric(self, loader: str, game: str) -> None:
        self.install_vanilla(game)
        version_id = fabric_id(loader, game)
        self.progress(0.02, f"Fabric {loader} для {game}...")
        profile = get_json(f"{FABRIC_META}/versions/loader/{game}/{loader}/profile/json")
        json_path = self.repo.versions_dir / version_id / f"{version_id}.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        self.run_tasks(self._library_tasks(profile), version_id)

    # ---------------------------------------------------------------- параллельная загрузка
    def _from_mirror(self, task: DownloadTask) -> bool:
        try:
            rel = task.dest.relative_to(self.game_dir)
        except ValueError:
            return False
        for mirror in self.mirrors:
            src = mirror / rel
            if src.is_file() and (task.size is None or src.stat().st_size == task.size):
                task.dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, task.dest)          # жёсткая ссылка: мгновенно и без лишнего места
                except OSError:
                    shutil.copy2(src, task.dest)
                return True
        return False

    def _fetch(self, task: DownloadTask) -> bool:
        if is_valid(task.dest, task.sha1, task.size):
            return False
        if self.mirrors and self._from_mirror(task) and is_valid(task.dest, task.sha1, task.size):
            return True
        return download(task.url, task.dest, task.sha1, task.size, self.cancel)

    def run_tasks(self, tasks: list[DownloadTask], title: str) -> None:
        unique = list({t.dest: t for t in tasks}.values())
        total = len(unique)
        if not total:
            return
        self.log(f"[Установка] {title}: проверка {total} файлов")
        done = downloaded = 0
        errors: list[str] = []
        t0, b0 = time.time(), bytes_downloaded()
        with ThreadPoolExecutor(max_workers=self.threads) as pool:
            futures = [pool.submit(self._fetch, t) for t in unique]
            try:
                for future in as_completed(futures):
                    if self.cancel.is_set():
                        raise Cancelled()
                    try:
                        if future.result():
                            downloaded += 1
                    except Cancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        errors.append(str(exc))
                    done += 1
                    if done % 25 == 0 or done == total:
                        got = bytes_downloaded() - b0
                        speed = f" · {format_speed(got, time.time() - t0)}" if got > 1 << 20 else ""
                        self.progress(done / total, f"{title}: {done}/{total} файлов{speed}")
            except Cancelled:
                self.cancel.set()
                pool.shutdown(wait=False, cancel_futures=True)
                raise
        got = bytes_downloaded() - b0
        self.log(f"[Установка] {title}: скачано {downloaded}, уже было {total - downloaded - len(errors)}"
                 + (f" · {got / 1048576:.0f} МБ за {time.time() - t0:.0f} с ({format_speed(got, time.time() - t0)})" if got > 1 << 20 else ""))
        if errors:
            for e in errors[:5]:
                self.log(f"[Ошибка] {e}")
            raise InstallError(f"Не удалось скачать {len(errors)} файлов")
