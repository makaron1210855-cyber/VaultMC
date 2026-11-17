"""
Forge и NeoForge.

Как ставим (так же, как Prism и другие лаунчеры, без окна официального установщика):
  1. скачиваем установщик *-installer.jar с официального maven;
  2. берём из него install_profile.json и version.json;
  3. качаем библиотеки (а те, что лежат внутри установщика в папке maven/, — распаковываем);
  4. запускаем «процессоры» из профиля (они патчат игру под Forge) — обычными командами java;
  5. кладём version.json в versions/<наш id>/ и копируем рядом jar игры.

Id версий в лаунчере:
  Forge     — "1.20.1-forge-47.3.0"
  NeoForge  — "neoforge-21.1.77" (для 1.20.1 был отдельный проект: "1.20.1-neoforge-47.1.106")
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

from .net import Cancelled, download, get_json
from .utils import maven_to_path, no_window_flags

FORGE_MAVEN = "https://maven.minecraftforge.net"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEO_MAVEN = "https://maven.neoforged.net/releases"
NEO_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
NEO_LEGACY_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/forge"

LOADERS = ("forge", "neoforge")
_FORGE_ID = re.compile(r"^(?P<mc>[\w.\-]+?)-forge-(?P<v>[\w.\-]+)$")
_NEO_ID = re.compile(r"^neoforge-(?P<v>[\w.\-]+)$")
_NEO_LEGACY_ID = re.compile(r"^(?P<mc>[\w.\-]+?)-neoforge-(?P<v>[\w.\-]+)$")


class ForgeError(Exception):
    pass


# ----------------------------------------------------------------------------- id версий
def loader_id(loader: str, mc: str, version: str) -> str:
    if loader == "forge":
        return f"{mc}-forge-{version}"
    if mc == "1.20.1":                       # NeoForge для 1.20.1 — ответвление Forge со своими номерами 47.1.x
        return f"{mc}-neoforge-{version}"
    return f"neoforge-{version}"


def parse_loader_id(version_id: str) -> tuple[str, str, str] | None:
    """(loader, версия игры, версия загрузчика) или None, если это не Forge/NeoForge."""
    m = _NEO_LEGACY_ID.match(version_id)
    if m:
        return "neoforge", m["mc"], m["v"]
    m = _FORGE_ID.match(version_id)
    if m:
        return "forge", m["mc"], m["v"]
    m = _NEO_ID.match(version_id)
    if m:
        return "neoforge", neoforge_game_version(m["v"]), m["v"]
    return None


def neoforge_game_version(v: str) -> str:
    """21.1.77 → 1.21.1; 21.0.10 → 1.21; 20.4.237 → 1.20.4.
    С 2026 года Mojang нумерует версии годами, NeoForge — «год.выпуск.хотфикс.сборка»: 26.1.0.5 → 26.1, 26.1.1.3 → 26.1.1."""
    parts = v.split("-")[0].split(".")
    major = parts[0]
    minor = parts[1] if len(parts) > 1 else "0"
    if major.isdigit() and int(major) >= 26:
        hotfix = parts[2] if len(parts) > 3 else "0"
        return f"{major}.{minor}" + (f".{hotfix}" if hotfix != "0" else "")
    return f"1.{major}" + (f".{minor}" if minor != "0" else "")


# ----------------------------------------------------------------------------- списки версий
def fetch_meta(cache: Path) -> dict:
    """{'forge': {mc: [версии, новые первыми]}, 'neoforge': {...}}. Без сети — из кэша."""
    meta: dict = {"forge": {}, "neoforge": {}}
    try:
        promos = get_json(FORGE_PROMOS, timeout=15).get("promos", {})
        for key, value in promos.items():
            mc, kind = key.rsplit("-", 1)
            lst = meta["forge"].setdefault(mc, [])
            if kind == "recommended":
                lst.insert(0, value)        # рекомендованная — первой
            elif value not in lst:
                lst.append(value)
    except Exception:  # noqa: BLE001
        pass
    try:
        for v in reversed(get_json(NEO_VERSIONS, timeout=15).get("versions", [])):
            meta["neoforge"].setdefault(neoforge_game_version(v), []).append(v)
        legacy = [v.split("-", 1)[1] for v in get_json(NEO_LEGACY_VERSIONS, timeout=15).get("versions", [])
                  if v.startswith("1.20.1-")]
        if legacy:
            meta["neoforge"]["1.20.1"] = list(reversed(legacy))
    except Exception:  # noqa: BLE001
        pass
    if meta["forge"] or meta["neoforge"]:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            pass
        return meta
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return meta


def installer_url(loader: str, mc: str, version: str) -> str:
    if loader == "forge":
        full = f"{mc}-{version}"
        return f"{FORGE_MAVEN}/net/minecraftforge/forge/{full}/forge-{full}-installer.jar"
    if mc == "1.20.1":
        full = f"1.20.1-{version}"
        return f"{NEO_MAVEN}/net/neoforged/forge/{full}/forge-{full}-installer.jar"
    return f"{NEO_MAVEN}/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar"


# ----------------------------------------------------------------------------- установка
def install(installer, loader: str, mc: str, version: str, java: Callable[[str], str]) -> str:
    """Ставит Forge/NeoForge. installer — core.installer.Installer (скачивание и прогресс),
    java(версия игры) — путь к java для запуска процессоров. Возвращает id версии."""
    repo, log = installer.repo, installer.log
    version_id = loader_id(loader, mc, version)
    vdir = repo.versions_dir / version_id
    if (vdir / f"{version_id}.json").exists() and (vdir / f"{version_id}.jar").exists():
        return version_id

    installer.install_vanilla(mc)                     # сначала обычная игра нужной версии
    libs = repo.game_dir / "libraries"
    title = "Forge" if loader == "forge" else "NeoForge"
    installer.progress(0.05, f"{title} {version}: скачиваю установщик...")
    tmp = Path(tempfile.mkdtemp(prefix="vaultmc-forge-"))
    try:
        jar_path = tmp / "installer.jar"
        url = installer_url(loader, mc, version)
        try:
            download(url, jar_path, cancel=installer.cancel)
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001
            if loader != "forge":
                raise
            # у старых Forge (до 1.10) в имени файла ещё раз повторяется версия игры: 1.8.9-11.15.1.2318-1.8.9
            download(url.replace(f"{mc}-{version}", f"{mc}-{version}-{mc}"), jar_path, cancel=installer.cancel)
        with zipfile.ZipFile(jar_path) as zf:
            profile = json.loads(zf.read("install_profile.json"))
            if "install" in profile and "versionInfo" in profile:      # старый формат (1.12.2 и раньше)
                data = _install_legacy(installer, zf, profile, libs)
            else:
                data = _install_modern(installer, zf, profile, libs, jar_path, tmp, mc, java, title)
        data["id"] = version_id
        data.setdefault("inheritsFrom", mc)
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{version_id}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        # своя копия jar игры: Forge ожидает файл <id>.jar (он в списке ignoreList у загрузчика)
        shutil.copy2(repo.versions_dir / mc / f"{mc}.jar", vdir / f"{version_id}.jar")
        log(f"[{title}] {version_id} установлен")
        return version_id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _lib_tasks(installer, libraries: list, zf: zipfile.ZipFile, libs: Path) -> list:
    """Задачи загрузки для библиотек; файлы из установщика (maven/...) сразу распаковываем."""
    from .installer import DownloadTask
    tasks = []
    names = set(zf.namelist())
    for lib in libraries:
        art = lib.get("downloads", {}).get("artifact")
        rel = (art or {}).get("path") or maven_to_path(lib["name"])
        dest = libs / rel
        inside = f"maven/{rel}"
        if inside in names and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(inside))
            continue
        url = (art or {}).get("url")
        if not url and not art:        # старый формат: базовый адрес в "url", иначе — библиотеки Mojang
            base = lib.get("url") or "https://libraries.minecraft.net/"
            url = base.rstrip("/") + "/" + rel
        if url:
            tasks.append(DownloadTask(url, dest, (art or {}).get("sha1"), (art or {}).get("size")))
    return tasks


def _install_modern(installer, zf, profile, libs, jar_path, tmp, mc, java, title) -> dict:
    data = json.loads(zf.read(profile.get("json", "/version.json").lstrip("/")))
    installer.progress(0.15, f"{title}: библиотеки...")
    installer.run_tasks(_lib_tasks(installer, profile.get("libraries", []) + data.get("libraries", []), zf, libs), title)

    # переменные для процессоров
    values: dict[str, str] = {}
    for key, entry in profile.get("data", {}).items():
        raw = entry.get("client", "") if isinstance(entry, dict) else str(entry)
        values[key] = _resolve_value(raw, zf, libs, tmp)
    client_jar = installer.repo.versions_dir / mc / f"{mc}.jar"
    values.update({"SIDE": "client", "MINECRAFT_JAR": str(client_jar), "MINECRAFT_VERSION": mc,
                   "ROOT": str(installer.repo.game_dir), "INSTALLER": str(jar_path), "LIBRARY_DIR": str(libs)})

    processors = [p for p in profile.get("processors", []) if "client" in p.get("sides", ["client"])]
    java_path = java(mc) if processors else ""
    for n, proc in enumerate(processors, 1):
        if installer.cancel.is_set():
            raise Cancelled()
        installer.progress(0.4 + 0.55 * n / max(1, len(processors)), f"{title}: подготовка игры ({n}/{len(processors)})...")
        outputs = {_subst(k, values, libs): _subst(v, values, libs) for k, v in proc.get("outputs", {}).items()}
        if outputs and all(Path(p).exists() and _sha1(Path(p)) == h.strip("'") for p, h in outputs.items()):
            continue                                               # уже сделано раньше
        jar = libs / maven_to_path(proc["jar"])
        classpath = [str(jar)] + [str(libs / maven_to_path(c)) for c in proc.get("classpath", [])]
        main_class = _main_class(jar)
        args = [_subst(a, values, libs) for a in proc.get("args", [])]
        cmd = [java_path, "-cp", os.pathsep.join(classpath), main_class, *args]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=no_window_flags())
        if res.returncode != 0:
            tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-15:])
            installer.log(f"[{title}] Процессор {proc['jar']} упал:\n{tail}")
            raise ForgeError(f"{title}: не удалось подготовить игру (шаг {n} из {len(processors)}). Подробности — в «Консоли».")
        for p, h in outputs.items():
            if Path(p).exists() and _sha1(Path(p)) != h.strip("'"):
                raise ForgeError(f"{title}: файл {Path(p).name} получился повреждённым")
    return data


def _install_legacy(installer, zf, profile, libs) -> dict:
    """Forge 1.12.2 и старше: версия лежит прямо в профиле, основной jar — внутри установщика."""
    info = profile["install"]
    data = profile["versionInfo"]
    universal = libs / maven_to_path(info["path"])
    universal.parent.mkdir(parents=True, exist_ok=True)
    universal.write_bytes(zf.read(info["filePath"]))
    rel = maven_to_path(info["path"])
    clean = []
    for lib in data.get("libraries", []):
        if lib.get("clientreq", True) is False and lib.get("serverreq"):
            continue                                   # только для сервера — игре не нужна
        if lib.get("name") == info["path"]:
            # основной jar Forge уже распакован из установщика: пустой url — «не качать при проверке файлов»
            lib = {"name": lib["name"], "downloads": {"artifact": {"path": rel, "url": ""}}}
        clean.append(lib)
    data["libraries"] = clean
    installer.run_tasks(_lib_tasks(installer, [lib for lib in clean if lib.get("name") != info["path"]], zf, libs), "Forge")
    return data


def _resolve_value(raw: str, zf, libs: Path, tmp: Path) -> str:
    if raw.startswith("[") and raw.endswith("]"):
        return str(libs / maven_to_path(raw[1:-1]))
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    if raw.startswith("/"):                         # файл внутри установщика
        target = tmp / "data" / raw.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(raw.lstrip("/")))
        return str(target)
    return raw


def _subst(arg: str, values: dict, libs: Path) -> str:
    if arg.startswith("[") and arg.endswith("]"):
        return str(libs / maven_to_path(arg[1:-1]))
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m[1], m[0]), arg)


def _main_class(jar: Path) -> str:
    with zipfile.ZipFile(jar) as zf:
        for line in zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace").splitlines():
            if line.startswith("Main-Class:"):
                return line.split(":", 1)[1].strip()
    raise ForgeError(f"В {jar.name} не указан Main-Class")


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["LOADERS", "ForgeError", "fetch_meta", "install", "installer_url", "loader_id", "parse_loader_id",
           "neoforge_game_version"]
