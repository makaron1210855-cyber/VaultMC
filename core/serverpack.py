"""Сервер из сборки: папка выделенного сервера той же версии игры, с тем же загрузчиком и модами (без клиентских).

Папка: ~/.vaultmc/servers/<id сборки>/. Повторная сборка («Обновить сервер») меняет загрузчик, моды и настройки модов,
но мир, server.properties, списки игроков и операторов не трогает: это данные сервера, а не сборки.

Загрузчики:
  vanilla  — server.jar с серверов Mojang (из описания версии);
  fabric   — server.jar + fabric-server-launch.jar с meta.fabricmc.net (библиотеки он докачает при первом запуске);
  quilt    — официальный установщик Quilt: install server ... --download-server;
  forge / neoforge — официальный установщик с --installServer (сам кладёт server.jar и библиотеки).
Как запускать, пишем в vaultmc-server.json (поле launch — аргументы java после памяти) и в start.bat / start.sh.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .net import download, get_json
from .utils import LAUNCHER_HOME, no_window_flags, offline_uuid

SERVERS_DIR = LAUNCHER_HOME / "servers"
MANIFEST = "vaultmc-server.json"
FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_MAVEN = "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer"
EULA_URL = "https://aka.ms/MinecraftEULA"
# что из папки сборки нужно серверу кроме модов (настройки модов, скрипты KubeJS/CraftTweaker)
COPY_DIRS = ("config", "defaultconfigs", "kubejs", "scripts")
SKIP_WORLD = {"session.lock"}
SKIP_WORLD_DIRS = {"DistantHorizons"}
_QUILT_ID = re.compile(r"^quilt-loader-([^-]+)-(.+)$")
_NICK = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class ServerError(Exception):
    pass


@dataclass
class ServerOptions:
    mods: list[str] = field(default_factory=list)   # файлы из mods/ сборки, которые кладём на сервер
    world: str = ""            # папка мира из saves/ сборки ("" — сервер создаст новый)
    replace_world: bool = False
    ram_gb: int = 4
    port: int = 25565
    whitelist: list[str] = field(default_factory=list)   # пусто — пускать всех
    op: str = ""               # ник оператора (обычно сам игрок)
    eula: bool = False

    @classmethod
    def parse(cls, raw: dict) -> "ServerOptions":
        raw = raw if isinstance(raw, dict) else {}
        nicks = [str(n).strip() for n in raw.get("whitelist") or [] if isinstance(n, str)]
        op = str(raw.get("op") or "").strip()
        try:
            port = int(raw.get("port") or 25565)
            ram = int(raw.get("ram_gb") or 4)
        except (TypeError, ValueError):
            raise ServerError("Порт и память — числа") from None
        if not 1024 <= port <= 65535:
            raise ServerError("Порт — число от 1024 до 65535")
        bad = [n for n in nicks + ([op] if op else []) if not _NICK.match(n)]
        if bad:
            raise ServerError(f"Неверный ник: {bad[0]} (3–16 латинских букв, цифр или _)")
        world = str(raw.get("world") or "")
        if world and (Path(world).name != world or world in (".", "..")):
            raise ServerError("Неверное имя мира")
        mods = [str(m) for m in raw.get("mods") or [] if isinstance(m, str) and Path(str(m)).name == m]
        return cls(mods, world, bool(raw.get("replace_world")), max(1, min(64, ram)), port,
                   list(dict.fromkeys(nicks)), op, bool(raw.get("eula")))


def server_dir(instance_id: str) -> Path:
    return SERVERS_DIR / instance_id


def read_manifest(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("launch") else None
    except (OSError, ValueError):
        return None


def loader_of(inst) -> tuple[str, str, str]:
    """(загрузчик, версия игры, версия загрузчика) сборки."""
    from . import forge
    from .versions import parse_fabric_id
    vid = inst.version_id
    if inst.loader == "fabric" and parse_fabric_id(vid):
        ver, mc = parse_fabric_id(vid)
        return "fabric", mc, ver
    m = _QUILT_ID.match(vid)
    if inst.loader == "quilt" and m:
        return "quilt", m.group(2), m.group(1)
    parsed = forge.parse_loader_id(vid)
    if inst.loader in ("forge", "neoforge") and parsed:
        return parsed
    if inst.loader == "vanilla":
        return "vanilla", inst.game_version, ""
    raise ServerError(f"Не понял версию загрузчика сборки ({vid}) — сервер для неё собрать не получится")


def free_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


# ------------------------------------------------------------------ установка загрузчика
class Builder:
    def __init__(self, repo, java_for: Callable[[int], str], log: Callable[[str], None],
                 progress: Callable[[float, str], None], cancel=None):
        self.repo, self.java_for, self.log, self.progress, self.cancel = repo, java_for, log, progress, cancel

    def _version_json(self, mc: str) -> dict:
        path = self.repo.versions_dir / mc / f"{mc}.json"
        if not path.exists():
            entry = self.repo.remote_entry(mc)
            if not entry:
                raise ServerError(f"Нет описания версии {mc} — нужен интернет")
            download(entry.url, path, sha1=entry.sha1 or None, cancel=self.cancel)
        return json.loads(path.read_text(encoding="utf-8"))

    def java_major(self, version_id: str, mc: str) -> int:
        for vid in (version_id, mc):
            try:
                major = self.repo.load_version_json(vid).get("javaVersion", {}).get("majorVersion")
                if major:
                    return int(major)
            except (OSError, ValueError, KeyError):
                continue
        return int(self._version_json(mc).get("javaVersion", {}).get("majorVersion", 8))

    def vanilla_jar(self, mc: str, dest: Path) -> None:
        info = self._version_json(mc).get("downloads", {}).get("server")
        if not info:
            raise ServerError(f"Для Minecraft {mc} Mojang не выпускал сервер")
        self.progress(0.2, f"Сервер Minecraft {mc}...")
        download(info["url"], dest, info.get("sha1"), info.get("size"), cancel=self.cancel)

    def install(self, folder: Path, loader: str, mc: str, ver: str, java: str) -> list[str]:
        """Ставит загрузчик в папку и возвращает аргументы запуска (после памяти)."""
        if loader == "vanilla":
            self.vanilla_jar(mc, folder / "server.jar")
            return ["-jar", "server.jar", "nogui"]
        if loader == "fabric":
            self.vanilla_jar(mc, folder / "server.jar")
            installers = get_json(f"{FABRIC_META}/versions/installer")
            inst_ver = next((i["version"] for i in installers if i.get("stable")), installers[0]["version"])
            self.progress(0.4, f"Fabric {ver} для сервера...")
            (folder / "fabric-server-launch.jar").unlink(missing_ok=True)   # у файла нет хэша: старую версию не узнать
            download(f"{FABRIC_META}/versions/loader/{mc}/{ver}/{inst_ver}/server/jar",
                     folder / "fabric-server-launch.jar", cancel=self.cancel)
            (folder / "fabric-server-launcher.properties").write_text("serverJar=server.jar\n", encoding="utf-8")
            return ["-jar", "fabric-server-launch.jar", "nogui"]
        if loader == "quilt":
            return self._quilt(folder, mc, ver, java)
        return self._forge(folder, loader, mc, ver, java)

    def _run_installer(self, cmd: list[str], folder: Path, what: str) -> None:
        self.log(f"[Сервер] {what}: {' '.join(cmd[-4:])}")
        proc = subprocess.Popen(cmd, cwd=str(folder), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=no_window_flags())
        tail: list[str] = []
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            tail = (tail + [line])[-15:]
            if self.cancel is not None and self.cancel.is_set():
                proc.kill()
                from .net import Cancelled
                raise Cancelled()
        if proc.wait() != 0:
            for line in tail:
                self.log(f"[Сервер] {line}")
            raise ServerError(f"{what} завершился с ошибкой (код {proc.returncode}) — подробности в «Консоли»")

    def _quilt(self, folder: Path, mc: str, ver: str, java: str) -> list[str]:
        text = _get_text(f"{QUILT_MAVEN}/maven-metadata.xml")
        m = re.search(r"<release>([^<]+)</release>", text) or re.search(r"<latest>([^<]+)</latest>", text)
        if not m:
            raise ServerError("Не нашёл установщик Quilt")
        iv = m.group(1)
        jar = LAUNCHER_HOME / "cache" / f"quilt-installer-{iv}.jar"
        self.progress(0.3, "Установщик Quilt...")
        download(f"{QUILT_MAVEN}/{iv}/quilt-installer-{iv}.jar", jar, cancel=self.cancel)
        self.progress(0.5, f"Quilt {ver} для сервера...")
        self._run_installer([java, "-jar", str(jar), "install", "server", mc, ver, "--download-server",
                             f"--install-dir={folder}"], folder, "Установщик Quilt")
        if not (folder / "quilt-server-launch.jar").exists():
            raise ServerError("Установщик Quilt не создал quilt-server-launch.jar")
        if not (folder / "server.jar").is_file():
            # на проверке 20.11.2026 установщик при «--download-server» иногда не клал server.jar (Quilt падал с «Missing game jar») —
            # докачиваем сами, как для Fabric; quilt-server-launcher.properties по умолчанию ищет именно server.jar
            self.log("[Сервер] установщик Quilt не положил server.jar — скачиваю сам")
            self.vanilla_jar(mc, folder / "server.jar")
        return ["-jar", "quilt-server-launch.jar", "nogui"]

    def _forge(self, folder: Path, loader: str, mc: str, ver: str, java: str) -> list[str]:
        from . import forge
        url = forge.installer_url(loader, mc, ver)
        jar = LAUNCHER_HOME / "cache" / Path(url).name
        self.progress(0.3, f"Установщик {loader.capitalize()}...")
        download(url, jar, cancel=self.cancel)
        self.progress(0.45, f"{'Forge' if loader == 'forge' else 'NeoForge'} {ver} для сервера (1–3 минуты)...")
        self._run_installer([java, "-jar", str(jar), "--installServer", str(folder)], folder,
                            "Установщик " + ("Forge" if loader == "forge" else "NeoForge"))
        for leftover in ("installer.log", jar.name + ".log"):
            (folder / leftover).unlink(missing_ok=True)
        return forge_launch_args(folder, ver)


def forge_launch_args(folder: Path, ver: str) -> list[str]:
    """Аргументы запуска сервера, который поставил установщик Forge/NeoForge.
    1.17+: файл аргументов в libraries (@…/win_args.txt), раньше — forge-<версия>.jar в корне папки."""
    args = sorted(folder.glob("libraries/**/win_args.txt"), key=lambda p: (ver not in str(p), len(str(p))))
    if args:
        rel = args[0].relative_to(folder).as_posix()
        return [f"@{rel}", "nogui"]
    jars = [p for p in folder.glob("*.jar") if "installer" not in p.name.lower()
            and re.match(r"(forge|neoforge)-", p.name, re.I)]
    jars.sort(key=lambda p: (ver not in p.name, "universal" in p.name, p.name))
    if jars:
        return ["-jar", jars[0].name, "nogui"]
    raise ServerError("Установщик отработал, но запускаемый файл сервера не найден")


def _get_text(url: str) -> str:
    import urllib.request
    from .net import USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(1 << 20).decode("utf-8", "replace")


# ------------------------------------------------------------------ файлы сервера
def set_properties(path: Path, values: dict, only_missing: dict) -> None:
    """server.properties: values — записываем всегда, only_missing — только если ключа ещё нет."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else [
        "#Minecraft server properties (VaultMC)"]
    seen: set[str] = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
            continue
        if key:
            seen.add(key)
        out.append(line)
    out += [f"{k}={v}" for k, v in {**only_missing, **values}.items() if k not in seen]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_players(folder: Path, opts: ServerOptions) -> None:
    if opts.whitelist:
        nicks = list(dict.fromkeys(opts.whitelist + ([opts.op] if opts.op else [])))
        data = [{"uuid": offline_uuid(n), "name": n} for n in nicks]
        (folder / "whitelist.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if opts.op:
        path = folder / "ops.json"
        try:
            ops = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, ValueError):
            ops = []
        ops = [o for o in ops if isinstance(o, dict)]
        if not any(str(o.get("name", "")).lower() == opts.op.lower() for o in ops):
            ops.append({"uuid": offline_uuid(opts.op), "name": opts.op, "level": 4, "bypassesPlayerLimit": True})
            path.write_text(json.dumps(ops, indent=2), encoding="utf-8")


def copy_world(src: Path, dest: Path) -> None:
    def ignore(directory, names):
        skip = [n for n in names if n in SKIP_WORLD or n in SKIP_WORLD_DIRS or n.startswith("DistantHorizons.sqlite")]
        return skip
    shutil.copytree(src, dest, ignore=ignore)


def sync_mods(src_dir: Path, dest_dir: Path, wanted: list[str], previous: list[str]) -> list[str]:
    """Кладёт выбранные моды. Удаляет только те, что положил сам лаунчер в прошлый раз (свои моды владельца сервера,
    добавленные в папку руками, остаются)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    present = [n for n in wanted if (src_dir / n).is_file()]
    for name in previous:
        if name not in present:
            (dest_dir / name).unlink(missing_ok=True)
    for name in present:
        src, dst = src_dir / name, dest_dir / name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)
    return present


def write_scripts(folder: Path, launch: list[str], ram: int, java_major: int, title: str) -> None:
    mem = f"-Xmx{ram}G -Xms{min(ram, 1)}G"
    args = " ".join(launch)
    (folder / "start.bat").write_text(
        "@echo off\r\ncd /d \"%~dp0\"\r\n"
        f"rem Нужна Java {java_major}. Свою Java можно указать: set JAVA=C:\\путь\\java.exe\r\n"
        "if \"%JAVA%\"==\"\" set JAVA=java\r\n"
        f"\"%JAVA%\" {mem} {args}\r\npause\r\n", encoding="utf-8")
    unix_args = args.replace("win_args.txt", "unix_args.txt")
    sh = folder / "start.sh"
    sh.write_bytes((
        "#!/bin/sh\ncd \"$(dirname \"$0\")\"\n"
        f"# Java {java_major}: sudo apt install openjdk-{java_major}-jre-headless\n"
        f"exec \"${{JAVA:-java}}\" {mem} {unix_args}\n").encode("utf-8"))
    (folder / "README.txt").write_text(
        f"Сервер «{title}» собран лаунчером VaultMC.\n\n"
        f"Нужна Java {java_major}.\n"
        "Windows: запустите start.bat.\n"
        f"Linux (VPS): sudo apt install openjdk-{java_major}-jre-headless, потом: chmod +x start.sh && ./start.sh\n"
        "Порт и другие настройки — в server.properties. Игроки без лицензии заходят, потому что online-mode=false:\n"
        "включите white-list=true и добавьте друзей (whitelist add <ник>), чтобы чужие не зашли под вашим ником.\n\n"
        f"Server \"{title}\" built by VaultMC. Needs Java {java_major}. Windows: start.bat. Linux: ./start.sh\n",
        encoding="utf-8")


def build(inst, builder: Builder, opts: ServerOptions) -> dict:
    """Собирает или обновляет сервер сборки. Возвращает данные о готовом сервере."""
    loader, mc, ver = loader_of(inst)
    folder = server_dir(inst.id)
    folder.mkdir(parents=True, exist_ok=True)
    old = read_manifest(folder) or {}
    major = builder.java_major(inst.version_id, mc)
    same = (old.get("loader"), old.get("mc"), old.get("loader_version")) == (loader, mc, ver)
    launch = old.get("launch") if same else None
    java = old.get("java") if same and Path(str(old.get("java") or "")).is_file() else ""
    if not java:
        java = builder.java_for(major)
    if not launch or not _launch_target_ok(folder, launch):
        builder.log(f"[Сервер] {loader} {mc} {ver}".rstrip() + f", Java {major}")
        launch = builder.install(folder, loader, mc, ver, java)
    builder.progress(0.75, "Моды и настройки для сервера...")
    mods = sync_mods(inst.mods_dir, folder / "mods", opts.mods, old.get("mods") or []) if loader != "vanilla" else []
    for name in COPY_DIRS:
        src = inst.game_dir / name
        if src.is_dir():
            shutil.copytree(src, folder / name, dirs_exist_ok=True)
    world_note = _place_world(inst, folder, opts)
    if opts.eula:
        (folder / "eula.txt").write_text(f"# {EULA_URL}\neula=true\n", encoding="utf-8")
    set_properties(folder / "server.properties",
                   {"server-port": opts.port, "white-list": str(bool(opts.whitelist)).lower(),
                    "enforce-whitelist": str(bool(opts.whitelist)).lower()},
                   {"online-mode": "false", "motd": f"VaultMC: {inst.name}"[:59], "level-name": "world",
                    "max-players": "10", "enable-status": "true", "spawn-protection": "0"})
    write_players(folder, opts)
    write_scripts(folder, launch, opts.ram_gb, major, inst.name)
    manifest = {"instance": inst.id, "name": inst.name, "loader": loader, "mc": mc, "loader_version": ver,
                "java_major": major, "java": java, "launch": launch, "mods": mods,
                "skipped": sorted(f.name for f in inst.mods_dir.glob("*.jar") if f.name not in mods) if loader != "vanilla" else [], "ram_gb": opts.ram_gb, "port": opts.port,
                "built_at": time.time()}
    (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    builder.progress(1.0, "Сервер готов")
    return {**manifest, "dir": str(folder), "world_note": world_note}


def _launch_target_ok(folder: Path, launch: list[str]) -> bool:
    for i, a in enumerate(launch):
        if a == "-jar" and i + 1 < len(launch):
            return (folder / launch[i + 1]).is_file()
        if a.startswith("@"):
            return (folder / a[1:]).is_file()
    return False


def _place_world(inst, folder: Path, opts: ServerOptions) -> str:
    if not opts.world:
        return ""
    src = inst.game_dir / "saves" / opts.world
    if not (src / "level.dat").is_file():
        raise ServerError(f"Мир «{opts.world}» не найден в сборке")
    dest = folder / "world"
    if dest.exists():
        if not opts.replace_world:
            return "kept"
        backup = folder / f"world-old-{time.strftime('%Y%m%d-%H%M%S')}"
        dest.rename(backup)
    copy_world(src, dest)
    return "copied"


def export_zip(folder: Path, dest: Path, with_world: bool) -> int:
    """Архив папки сервера для VPS. Журналы и отчёты о вылетах не кладём, мир — по желанию."""
    skip_top = {"logs", "crash-reports", "debug"} | (set() if with_world else {"world"})
    count = 0
    tmp = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder)
            top = rel.parts[0]
            if top in skip_top or top.startswith("world-old-") or path.name == "session.lock" or not path.is_file():
                continue
            info_name = rel.as_posix()
            if info_name == "start.sh":
                zi = zipfile.ZipInfo.from_file(path, info_name)
                zi.external_attr = 0o755 << 16
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, path.read_bytes())
            else:
                zf.write(path, info_name)
            count += 1
    tmp.replace(dest)
    return count
