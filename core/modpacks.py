"""Установка готовых сборок Modrinth (.mrpack) в новую сборку лаунчера. Поддерживаются Fabric и Vanilla."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from urllib.parse import quote

from .instances import Instance, InstanceManager
from .modsources import MODRINTH_API, ModSourceError
from .net import download_parallel, download, get_json
from .forge import loader_id
from .versions import fabric_id


def install_mrpack(project_id: str, title: str, manager: InstanceManager, tmp_dir: Path,
                   game_version: str = "", log=print) -> Instance:
    query = f"?game_versions={quote(json.dumps([game_version]))}" if game_version else ""
    versions = get_json(f"{MODRINTH_API}/project/{quote(project_id)}/version{query}")
    if not versions:
        raise ModSourceError(f"У сборки «{title}» нет версии для {game_version or 'этой игры'}")
    version = versions[0]
    file = next((f for f in version["files"] if f.get("primary")), version["files"][0])
    pack = tmp_dir / file["filename"]
    log(f"[Сборка] Скачиваю {file['filename']}")
    download(file["url"], pack, file.get("hashes", {}).get("sha1"), file.get("size"))

    inst = install_mrpack_file(pack, title, manager, log)
    pack.unlink(missing_ok=True)
    inst.notes = f"Сборка Modrinth: {title} {version.get('version_number', '')}"
    manager.save(inst)
    return inst


def install_mrpack_file(pack: Path, title: str, manager: InstanceManager, log=print) -> Instance:
    """Создаёт сборку из локального файла .mrpack (скачанного или импортированного)."""
    with zipfile.ZipFile(pack) as zf:
        index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
        deps = index.get("dependencies", {})
        game = deps.get("minecraft")
        if not game:
            raise ModSourceError("В сборке не указана версия Minecraft")
        if "quilt-loader" in deps:
            raise ModSourceError("Сборка требует Quilt — поддерживаются Fabric, Forge, NeoForge и Vanilla")
        if "fabric-loader" in deps:
            inst = manager.create(title, fabric_id(deps["fabric-loader"], game), game, "fabric")
        elif "neoforge" in deps or "forge" in deps:
            kind = "neoforge" if "neoforge" in deps else "forge"
            inst = manager.create(title, loader_id(kind, game, deps[kind]), game, kind)
        else:
            inst = manager.create(title, game, game, "vanilla")
        try:
            _fill_mrpack(zf, index, inst, log)
        except BaseException:
            manager.delete(inst)  # импорт сорвался — не оставляем полупустую сборку
            raise
    return inst


def _fill_mrpack(zf, index: dict, inst: Instance, log) -> None:
    root = inst.game_dir.resolve()

    files = [f for f in index.get("files", []) if f.get("env", {}).get("client") != "unsupported"]
    jobs = []
    for f in files:
        target = (inst.game_dir / f["path"]).resolve()
        if root not in target.parents or not f.get("downloads"):
            continue  # защита от путей вне папки сборки
        jobs.append((list(f["downloads"]), target, f.get("hashes", {}).get("sha1"), f.get("fileSize")))
    log(f"[Сборка] Скачиваю {len(jobs)} файлов в 8 потоков")
    errors = download_parallel(jobs, log, "Сборка", threads=8)
    if errors:
        for e in errors[:5]:
            log(f"[Ошибка] {e}")
        raise ModSourceError(f"Не удалось скачать {len(errors)} файлов сборки — проверьте интернет и попробуйте ещё раз")

    for prefix in ("overrides/", "client-overrides/"):
        for member in zf.namelist():
            if member.startswith(prefix) and not member.endswith("/"):
                target = (inst.game_dir / member[len(prefix):]).resolve()
                if root in target.parents:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
