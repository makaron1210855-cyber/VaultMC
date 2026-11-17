"""
Экспорт и импорт сборок в форматах других лаунчеров (как в Prism Launcher):

  Modrinth (.mrpack)  — моды, найденные на Modrinth, записываются ссылками (файл маленький),
                        остальные кладутся в overrides/. Открывается в Prism, Modrinth App, ATLauncher.
  CurseForge (.zip)   — manifest.json + overrides/. С API-ключом CurseForge моды записываются
                        ссылками на CurseForge, без ключа — кладутся файлами в overrides/.
  VaultMC (.vaultpack) — родной формат лаунчера (старые .neonpack тоже открываются).

Импорт сам определяет формат по содержимому архива.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from .instances import PACK_EXT, Instance, InstanceManager
from .modpacks import install_mrpack_file
from .modsources import CURSEFORGE_API, MODRINTH_API, ModSourceError
from .net import download, get_json, post_json
from .forge import loader_id, parse_loader_id
from .versions import fabric_id, parse_fabric_id

CONTENT_DIRS = ("mods", "resourcepacks", "shaderpacks")
OVERRIDE_DIRS = ("config", "defaultconfigs", "kubejs", "scripts")
OVERRIDE_FILES = ("options.txt", "servers.dat")


CONTENT_EXT = {"mods": (".jar",), "resourcepacks": (".zip",), "shaderpacks": (".zip",)}


def _files(inst: Instance, folder: str) -> list[Path]:
    """Файлы контента сборки. Служебные файлы лаунчера (.vaultmc-*.json и т.п.) и выключенные моды не берём."""
    d = inst.game_dir / folder
    ext = CONTENT_EXT.get(folder)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")
                  and not p.name.endswith(".disabled") and (ext is None or p.name.lower().endswith(ext)))


def _add_overrides(zf: zipfile.ZipFile, inst: Instance, prefix: str = "overrides") -> None:
    for folder in OVERRIDE_DIRS:
        d = inst.game_dir / folder
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file():
                    zf.write(f, f"{prefix}/{f.relative_to(inst.game_dir).as_posix()}")
    for name in OVERRIDE_FILES:
        f = inst.game_dir / name
        if f.exists():
            zf.write(f, f"{prefix}/{name}")


def _loader_version(inst: Instance) -> str:
    parsed = parse_fabric_id(inst.version_id)
    if parsed:
        return parsed[0]
    forge = parse_loader_id(inst.version_id)
    return forge[2] if forge else ""


# ============================================================================ Modrinth .mrpack

def export_mrpack(inst: Instance, dest: Path, log=print) -> Path:
    dest = dest.with_suffix(".mrpack")
    content = [(folder, f) for folder in CONTENT_DIRS for f in _files(inst, folder)]
    hashes = {}
    for folder, f in content:
        data = f.read_bytes()
        hashes[f] = (hashlib.sha1(data).hexdigest(), hashlib.sha512(data).hexdigest(), len(data))
    known = {}
    if hashes:
        try:  # какие файлы есть на Modrinth — по хэшу
            known = post_json(f"{MODRINTH_API}/version_files",
                              {"hashes": [h[0] for h in hashes.values()], "algorithm": "sha1"})
        except Exception as exc:  # noqa: BLE001
            log(f"[Экспорт] Modrinth недоступен ({exc}) — все файлы пойдут внутрь архива")
    files_json = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, f in content:
            sha1, sha512, size = hashes[f]
            version = known.get(sha1)
            remote = next((x for x in (version or {}).get("files", []) if x.get("hashes", {}).get("sha1") == sha1), None)
            if remote:
                files_json.append({"path": f"{folder}/{f.name}", "hashes": {"sha1": sha1, "sha512": sha512},
                                   "env": {"client": "required", "server": "required"},
                                   "downloads": [remote["url"]], "fileSize": size})
            else:
                zf.write(f, f"overrides/{folder}/{f.name}")
        _add_overrides(zf, inst)
        deps = {"minecraft": inst.game_version}
        if inst.loader in ("fabric", "forge", "neoforge") and _loader_version(inst):
            deps["fabric-loader" if inst.loader == "fabric" else inst.loader] = _loader_version(inst)
        zf.writestr("modrinth.index.json", json.dumps({
            "formatVersion": 1, "game": "minecraft", "versionId": "1.0.0", "name": inst.name,
            "summary": inst.notes or "Сборка из VaultMC", "files": files_json, "dependencies": deps,
        }, indent=2, ensure_ascii=False))
    log(f"[Экспорт] {dest.name}: {len(files_json)} ссылок на Modrinth, {len(content) - len(files_json)} файлов внутри")
    return dest


# ============================================================================ CurseForge .zip

def cf_fingerprint(data: bytes) -> int:
    """Отпечаток файла CurseForge: MurmurHash2 (seed 1) без пробельных байтов."""
    data = data.translate(None, b"\t\n\r ")
    m, length = 0x5BD1E995, len(data)
    h = (1 ^ length) & 0xFFFFFFFF
    n4 = length // 4 * 4
    for k in memoryview(data[:n4]).cast("I"):
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> 24
        k = (k * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
    rest = length - n4
    if rest == 3:
        h ^= data[n4 + 2] << 16
    if rest >= 2:
        h ^= data[n4 + 1] << 8
    if rest >= 1:
        h ^= data[n4]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


def export_curseforge(inst: Instance, dest: Path, api_key: str = "", log=print) -> Path:
    dest = dest.with_suffix(".zip")
    mods = _files(inst, "mods")
    matches = {}
    if api_key and mods:
        try:
            prints = {cf_fingerprint(f.read_bytes()): f for f in mods}
            result = post_json(f"{CURSEFORGE_API}/fingerprints/432", {"fingerprints": list(prints)},
                               headers={"x-api-key": api_key})
            for match in result.get("data", {}).get("exactMatches", []):
                file = match.get("file", {})
                if file.get("fileFingerprint") in prints:
                    matches[prints[file["fileFingerprint"]]] = (file["modId"], file["id"])
        except Exception as exc:  # noqa: BLE001
            log(f"[Экспорт] CurseForge недоступен ({exc}) — моды пойдут внутрь архива")
    loader = [{"id": f"{inst.loader}-{_loader_version(inst)}", "primary": True}] \
        if inst.loader in ("fabric", "forge", "neoforge") and _loader_version(inst) else []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in mods:
            if f not in matches:
                zf.write(f, f"overrides/mods/{f.name}")
        for folder in ("resourcepacks", "shaderpacks"):
            for f in _files(inst, folder):
                zf.write(f, f"overrides/{folder}/{f.name}")
        _add_overrides(zf, inst)
        zf.writestr("manifest.json", json.dumps({
            "minecraft": {"version": inst.game_version, "modLoaders": loader},
            "manifestType": "minecraftModpack", "manifestVersion": 1, "name": inst.name, "version": "1.0.0",
            "author": "VaultMC", "overrides": "overrides",
            "files": [{"projectID": pid, "fileID": fid, "required": True} for pid, fid in matches.values()],
        }, indent=2, ensure_ascii=False))
    log(f"[Экспорт] {dest.name}: {len(matches)} ссылок на CurseForge, {len(mods) - len(matches)} модов внутри")
    return dest


def _cf_loader(loaders: list[str], game: str) -> tuple[str, str]:
    """modLoaders из manifest.json CurseForge → (id версии, загрузчик): 'forge-47.3.0', 'neoforge-21.1.77', 'fabric-0.16.5'."""
    for raw in loaders:
        kind, _, ver = raw.partition("-")
        if kind == "fabric" and ver:
            return fabric_id(ver, game), "fabric"
        if kind in ("forge", "neoforge") and ver:
            if ver.startswith(game + "-"):          # бывает «neoforge-1.20.1-47.1.106»
                ver = ver[len(game) + 1:]
            return loader_id(kind, game, ver), kind
    if loaders:
        raise ModSourceError(f"Сборка требует {loaders[0].split('-')[0]} — поддерживаются Fabric, Forge, NeoForge и Vanilla")
    return game, "vanilla"


def import_curseforge(pack: Path, manager: InstanceManager, api_key: str = "", log=print) -> tuple[Instance, list[str]]:
    created: list[Instance] = []
    try:
        return _import_curseforge(pack, manager, api_key, log, created)
    except BaseException:
        for inst in created:
            manager.delete(inst)  # импорт сорвался — не оставляем полупустую сборку
        raise


def _import_curseforge(pack: Path, manager: InstanceManager, api_key: str, log, created: list) -> tuple[Instance, list[str]]:
    with zipfile.ZipFile(pack) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        game = manifest["minecraft"]["version"]
        loaders = [l.get("id", "") for l in manifest["minecraft"].get("modLoaders", [])]
        name = manifest.get("name") or pack.stem
        version_id, loader = _cf_loader(loaders, game)
        inst = manager.create(name, version_id, game, loader)
        created.append(inst)
        root = inst.game_dir.resolve()
        prefix = (manifest.get("overrides") or "overrides").rstrip("/") + "/"
        for member in zf.namelist():
            if member.startswith(prefix) and not member.endswith("/"):
                target = (inst.game_dir / member[len(prefix):]).resolve()
                if root in target.parents:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    problems: list[str] = []
    files = manifest.get("files", [])
    if files and not api_key:
        problems.append(f"{len(files)} модов не скачано: для CurseForge нужен API-ключ (Настройки)")
        return inst, problems
    from concurrent.futures import ThreadPoolExecutor
    done = [0]

    def one(entry):          # каждый мод: узнать ссылку у CurseForge и скачать — 8 модов одновременно
        try:
            info = get_json(f"{CURSEFORGE_API}/mods/{entry['projectID']}/files/{entry['fileID']}",
                            headers={"x-api-key": api_key}).get("data", {})
            if not info.get("downloadUrl"):
                return f"{info.get('displayName') or entry['projectID']}: автор запретил скачивание из лаунчеров"
            download(info["downloadUrl"], inst.mods_dir / info["fileName"])
            done[0] += 1
            log(f"[Импорт] {done[0]}/{len(files)} {info['fileName']}")
            return None
        except Exception as exc:  # noqa: BLE001
            return f"мод {entry.get('projectID')}: {exc}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        problems += [p for p in pool.map(one, files) if p]
    return inst, problems


# ============================================================================ импорт любого формата

def import_any(pack: Path, manager: InstanceManager, api_key: str = "", log=print) -> tuple[Instance, list[str]]:
    with zipfile.ZipFile(pack) as zf:
        names = set(zf.namelist())
    if "modrinth.index.json" in names:
        with zipfile.ZipFile(pack) as zf:
            title = json.loads(zf.read("modrinth.index.json").decode("utf-8")).get("name") or pack.stem
        return install_mrpack_file(pack, title, manager, log), []
    if "manifest.json" in names:
        return import_curseforge(pack, manager, api_key, log)
    if "instance.json" in names:
        return manager.import_pack(pack), []
    raise ModSourceError("Это не сборка: нужен файл .vaultpack, .mrpack или .zip сборки CurseForge")


def export_any(fmt: str, inst: Instance, dest: Path, manager: InstanceManager, api_key: str = "", log=print) -> Path:
    if fmt == "mrpack":
        return export_mrpack(inst, dest, log)
    if fmt == "curseforge":
        return export_curseforge(inst, dest, api_key, log)
    return manager.export_pack(inst, dest)


EXPORT_FORMATS = {"mrpack": ("Modrinth (mrpack)", ".mrpack"), "curseforge": ("CurseForge (zip)", ".zip"),
                  "vault": ("VaultMC (vaultpack)", PACK_EXT)}
