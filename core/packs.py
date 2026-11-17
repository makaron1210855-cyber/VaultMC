"""
Ресурспаки и шейдеры сборки: список, включение, порядок, выбор шейдера, добавление, удаление, обновления.

Где что лежит:
  resourcepacks/<файл .zip или папка>   — сами ресурспаки
  options.txt → resourcePacks:["vanilla","file/имя.zip",...]  — какие включены; позже в списке = важнее
  shaderpacks/<файл .zip или папка>     — сами шейдеры
  config/iris.properties → enableShaders=true|false, shaderPack=имя  — активный шейдер (Iris)

Шейдеры OptiFine хранят выбор в optionsshaders.txt — здесь пока не поддерживаются, только Iris.
Обновления — по SHA-1 файла через Modrinth (тот же способ, что и для модов).
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import repair

KINDS = {"resourcepack": "resourcepacks", "shader": "shaderpacks"}
# загрузчики, под которые Modrinth ищет файлы (проверено по /v2/tag/loader)
MODRINTH_LOADERS = {"resourcepack": ["minecraft"], "shader": ["iris", "optifine", "canvas", "vanilla"]}
BACKUP_DIR = ".vaultmc_packbackup"
_SKIP_SUFFIXES = (".part", ".vaultnew", ".tmp", ".vaultmc.bak")


def _folder(game_dir: Path, kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError("Неизвестный тип: " + str(kind))
    return game_dir / KINDS[kind]


def _entries(folder: Path) -> list[Path]:
    """Файлы и папки паков (без служебных и скрытых)."""
    if not folder.is_dir():
        return []
    out = []
    for p in folder.iterdir():
        if p.name.startswith(".") or p.name.endswith(_SKIP_SUFFIXES):
            continue
        if p.is_dir() or p.suffix.lower() == ".zip":
            out.append(p)
    return sorted(out, key=lambda p: p.name.lower())


def _size_kb(path: Path) -> int:
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // 1024
    return path.stat().st_size // 1024


def _check_name(name: str) -> str:
    clean = Path(str(name)).name
    if not clean or clean in (".", ".."):
        raise ValueError("Неверное имя файла")
    return clean


# ---------------------------------------------------------------- options.txt (ресурспаки)
def _options_text(game_dir: Path) -> str:
    path = game_dir / "options.txt"
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _write_packs(game_dir: Path, packs: list[str]) -> None:
    path = game_dir / "options.txt"
    repair._backup_options(game_dir)
    text = _options_text(game_dir)
    line = "resourcePacks:" + json.dumps(packs, ensure_ascii=False)
    if re.search(r"^resourcePacks:.*$", text, re.M):
        text = re.sub(r"^resourcePacks:.*$", lambda m: line, text, count=1, flags=re.M)
    else:
        text = (text.rstrip("\n") + "\n" if text else "") + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def enabled_packs(game_dir: Path) -> list[str]:
    """Имена включённых ресурспаков, от менее важного к более важному (порядок из options.txt)."""
    return [e[5:] for e in repair._options_packs(game_dir) if e.startswith("file/")]


def list_resourcepacks(game_dir: Path) -> list[dict]:
    folder = _folder(game_dir, "resourcepack")
    packs = repair._options_packs(game_dir)
    enabled = [e[5:] for e in packs if e.startswith("file/")]
    rows = []
    for p in _entries(folder):
        try:
            err = repair._pack_meta_error(p)
        except Exception:  # noqa: BLE001
            err = "файл не читается"
        rows.append({"name": p.name, "is_dir": p.is_dir(), "size": _size_kb(p), "enabled": p.name in enabled,
                     "order": enabled.index(p.name) if p.name in enabled else -1, "error": err})
    # сверху — включённые в порядке важности (самый важный первым), потом выключенные по имени
    rows.sort(key=lambda r: (not r["enabled"], -r["order"], r["name"].lower()))
    return rows


def set_resourcepack(game_dir: Path, name: str, enabled: bool) -> None:
    name = _check_name(name)
    if not (_folder(game_dir, "resourcepack") / name).exists():
        raise FileNotFoundError("Ресурспак не найден — возможно, его удалили вручную")
    packs = repair._options_packs(game_dir)
    entry = "file/" + name
    if enabled and entry not in packs:
        packs.append(entry)            # новый пак — самый важный (в конце списка)
    elif not enabled and entry in packs:
        packs.remove(entry)
    else:
        return
    _write_packs(game_dir, packs)


def move_resourcepack(game_dir: Path, name: str, step: int) -> None:
    """step = -1: выше по важности (к концу списка), +1: ниже. Меняется только позиция среди включённых."""
    name = _check_name(name)
    packs = repair._options_packs(game_dir)
    positions = [i for i, e in enumerate(packs) if e.startswith("file/")]
    ordered = [packs[i] for i in positions]
    entry = "file/" + name
    if entry not in ordered:
        raise ValueError("Ресурспак выключен — сначала включите его")
    i = ordered.index(entry)
    j = i + (1 if step > 0 else -1)
    if not 0 <= j < len(ordered):
        return
    ordered[i], ordered[j] = ordered[j], ordered[i]
    for pos, value in zip(positions, ordered):
        packs[pos] = value
    _write_packs(game_dir, packs)


# ---------------------------------------------------------------- шейдеры (Iris)
def _iris_path(game_dir: Path) -> Path:
    return repair._iris_file(game_dir)


def _set_iris(game_dir: Path, updates: dict[str, str]) -> None:
    path = _iris_path(game_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    for key, value in updates.items():
        found = False
        for n, line in enumerate(lines):
            if line.strip().startswith(key + "=") or line.strip().startswith(key + " "):
                lines[n] = f"{key}={value}"
                found = True
        if not found:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def shader_state(game_dir: Path) -> dict:
    props = repair._read_props(_iris_path(game_dir))
    return {"on": props.get("enableShaders", "").lower() == "true", "active": props.get("shaderPack", "")}


def list_shaders(game_dir: Path) -> list[dict]:
    state = shader_state(game_dir)
    rows = [{"name": p.name, "is_dir": p.is_dir(), "size": _size_kb(p),
             "active": state["on"] and p.name == state["active"]} for p in _entries(_folder(game_dir, "shader"))]
    return sorted(rows, key=lambda r: (not r["active"], r["name"].lower()))


def set_shader(game_dir: Path, name: str) -> None:
    """name = '' — выключить шейдеры (имя выбранного пакета остаётся в настройках)."""
    if not name:
        _set_iris(game_dir, {"enableShaders": "false"})
        return
    name = _check_name(name)
    if not (_folder(game_dir, "shader") / name).exists():
        raise FileNotFoundError("Шейдер не найден — возможно, его удалили вручную")
    _set_iris(game_dir, {"shaderPack": name, "enableShaders": "true"})


# ---------------------------------------------------------------- общие действия
def add_files(game_dir: Path, kind: str, sources: list[str]) -> list[str]:
    folder = _folder(game_dir, kind)
    folder.mkdir(parents=True, exist_ok=True)
    added = []
    for src in sources:
        p = Path(src)
        if p.is_dir():
            dest = folder / p.name
            if dest.exists():
                raise ValueError(f"«{p.name}» уже есть в сборке")
            shutil.copytree(p, dest)
        else:
            if p.suffix.lower() != ".zip":
                raise ValueError(f"«{p.name}»: нужен архив .zip")
            dest = folder / p.name
            if dest.exists():
                raise ValueError(f"«{p.name}» уже есть в сборке")
            shutil.copy2(p, dest)
        added.append(dest.name)
    return added


def delete(game_dir: Path, kind: str, name: str) -> None:
    name = _check_name(name)
    path = _folder(game_dir, kind) / name
    if not path.exists():
        raise FileNotFoundError("Файл уже удалён")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    if kind == "resourcepack":
        packs = repair._options_packs(game_dir)
        if "file/" + name in packs:
            packs.remove("file/" + name)
            _write_packs(game_dir, packs)
    else:
        state = shader_state(game_dir)
        if state["active"] == name:
            _set_iris(game_dir, {"enableShaders": "false"})


# ---------------------------------------------------------------- обновления
def check_updates(game_dir: Path, kind: str, game_version: str, log=print) -> list[dict]:
    """Какие паки (zip) можно обновить под эту версию игры. Папки-паки не проверяем: у них нет файла для SHA-1."""
    from .modsources import MODRINTH_API
    from .modupdates import _sha1
    from .net import post_json

    files = [p for p in _entries(_folder(game_dir, kind)) if p.is_file()]
    if not files:
        return []
    by_sha = {_sha1(p): p for p in files}
    try:
        result = post_json(f"{MODRINTH_API}/version_files/update",
                           {"hashes": list(by_sha), "algorithm": "sha1",
                            "loaders": MODRINTH_LOADERS[kind], "game_versions": [game_version]}) or {}
    except Exception as exc:  # noqa: BLE001
        log(f"[Обновления] Modrinth недоступен: {exc}")
        return []
    out = []
    for sha, version in result.items():
        path = by_sha.get(sha)
        if not path or not isinstance(version, dict):
            continue
        files_list = version.get("files") or []
        if any((f.get("hashes") or {}).get("sha1") == sha for f in files_list):
            continue                                      # уже последняя
        remote = next((f for f in files_list if f.get("primary")), None) or (files_list[0] if files_list else None)
        if not remote or not remote.get("filename"):
            continue
        out.append({"file": path.name, "title": path.stem, "project_id": str(version.get("project_id", "")),
                    "new_file": remote["filename"], "url": remote.get("url", ""),
                    "sha1": (remote.get("hashes") or {}).get("sha1", ""), "size": int(remote.get("size") or 0),
                    "version": version.get("version_number", ""), "kind": kind})
    out.sort(key=lambda u: u["title"].lower())
    return out


def apply_updates(game_dir: Path, kind: str, updates: list[dict], log=print) -> tuple[int, list[str]]:
    """Скачивает новые версии, старые убирает в .vaultmc_packbackup. Имя в списке и в настройках обновляется."""
    from .net import download

    folder = _folder(game_dir, kind)
    backup = game_dir / BACKUP_DIR
    done, errors = 0, []
    for u in updates:
        old = folder / _check_name(u["file"])
        new = folder / _check_name(u["new_file"])
        tmp = folder / (new.name + ".vaultnew")
        try:
            download(u["url"], tmp, u.get("sha1") or None, u.get("size") or None)
            saved = None
            if old.exists():
                backup.mkdir(parents=True, exist_ok=True)
                saved = backup / f"{int(time.time() * 1000)}_{old.name}"
                shutil.move(str(old), str(saved))
            try:
                tmp.replace(new)
            except OSError:
                if saved is not None:      # новый файл не встал — возвращаем старый на место
                    shutil.move(str(saved), str(old))
                raise
            _rename_refs(game_dir, kind, old.name, new.name)
            done += 1
            log(f"[Обновления] {u['title']}: {old.name} → {new.name}")
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            errors.append(f"{u['title']}: {exc}")
            log(f"[Обновления] {u['title']}: не удалось — {exc}")
    return done, errors


def _rename_refs(game_dir: Path, kind: str, old: str, new: str) -> None:
    if old == new:
        return
    if kind == "resourcepack":
        packs = repair._options_packs(game_dir)
        if "file/" + old in packs:
            packs = [("file/" + new if p == "file/" + old else p) for p in packs]
            _write_packs(game_dir, packs)
    else:
        state = shader_state(game_dir)
        if state["active"] == old:
            _set_iris(game_dir, {"shaderPack": new})
