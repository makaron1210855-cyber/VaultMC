"""Снимки сборки: моды, настройки модов, ресурспаки, шейдеры и options.txt в одну «точку», к которой можно вернуться.

Как хранится (папка <сборка>/snapshots):
  blobs/<sha1>        — содержимое файлов. Одинаковый файл хранится один раз, поэтому 20 снимков сборки
                        весят почти как один: между снимками обычно меняется пара модов и несколько настроек.
  <id>.json           — снимок: когда, почему, и список «путь → sha1, размер».
  hashes.json         — кэш «путь, размер, время изменения → sha1», чтобы не пересчитывать хэши сотен модов.
Миры в снимок не входят: у них свои копии (core/worlds.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

TRACKED_DIRS = ("mods", "config", "resourcepacks", "shaderpacks")
TRACKED_FILES = ("options.txt",)
KEEP = 20                          # снимков на сборку; старые удаляются вместе с ненужными файлами
SKIP_NAMES = {".vaultmc_mod_cache.json"}   # служебный кэш лаунчера — не настройки игрока
SNAP_DIR = "snapshots"


def _dir(inst_root: Path) -> Path:
    return inst_root / SNAP_DIR


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tracked(game_dir: Path) -> list[Path]:
    files = []
    for d in TRACKED_DIRS:
        base = game_dir / d
        if base.is_dir():
            files += [p for p in base.rglob("*") if p.is_file() and p.name not in SKIP_NAMES]
    files += [game_dir / f for f in TRACKED_FILES if (game_dir / f).is_file()]
    return files


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _scan(game_dir: Path, store: Path) -> dict[str, dict]:
    """Текущие файлы сборки: относительный путь → {sha1, size}. Хэши берутся из кэша, если файл не менялся."""
    cache_path = store / "hashes.json"
    cache = _read_json(cache_path, {})
    out, fresh = {}, {}
    for p in _tracked(game_dir):
        rel = p.relative_to(game_dir).as_posix()
        st = p.stat()
        key = f"{rel}|{st.st_size}|{st.st_mtime_ns}"
        sha = cache.get(key) or _sha1(p)
        fresh[key] = sha
        out[rel] = {"sha1": sha, "size": st.st_size}
    _write_json(cache_path, fresh)
    return out


def _manifests(store: Path) -> list[dict]:
    snaps = [_read_json(p, None) for p in store.glob("*.json") if p.name != "hashes.json"]
    return sorted((s for s in snaps if isinstance(s, dict) and "files" in s), key=lambda s: s["at"])


def create(inst_root: Path, game_dir: Path, reason: str, keep: int = KEEP) -> dict:
    """Сохраняет снимок. Если со времени прошлого снимка ничего не изменилось — новый не делается
    (возвращается прошлый с same=True). Возвращает краткие сведения о снимке."""
    store = _dir(inst_root)
    blobs = store / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    files = _scan(game_dir, store)
    snaps = _manifests(store)
    if snaps and snaps[-1]["files"] == files:
        return {**_summary(snaps[-1]), "same": True}
    for rel, meta in files.items():
        blob = blobs / meta["sha1"]
        if not blob.exists():
            tmp = blob.with_suffix(".tmp")
            shutil.copyfile(game_dir / rel, tmp)
            os.replace(tmp, blob)
    now = time.time()
    snap = {"id": f"{int(now * 1000)}", "at": now, "reason": str(reason)[:120], "files": files}
    _write_json(store / f"{snap['id']}.json", snap)
    _prune(store, keep)
    return _summary(snap)


def _prune(store: Path, keep: int) -> None:
    snaps = _manifests(store)
    for old in snaps[:-keep] if len(snaps) > keep else []:
        (store / f"{old['id']}.json").unlink(missing_ok=True)
    used = {m["sha1"] for s in _manifests(store) for m in s["files"].values()}
    for blob in (store / "blobs").iterdir():
        if blob.name not in used:
            blob.unlink(missing_ok=True)


def _summary(snap: dict) -> dict:
    files = snap["files"]
    return {"id": snap["id"], "at": snap["at"], "reason": snap.get("reason", ""),
            "mods": sum(1 for p in files if p.startswith("mods/") and p.endswith((".jar", ".jar.disabled"))),
            "size": sum(m["size"] for m in files.values())}


_VERSION_TAIL = re.compile(r"[-_+ ](mc)?v?\d.*$", re.I)


def _mod_key(name: str) -> str:
    """«sodium-fabric-0.5.8+mc1.20.1.jar» → «sodium-fabric»: так находим, что мод обновился, а не заменён другим."""
    base = name.removesuffix(".disabled").removesuffix(".jar")
    return _VERSION_TAIL.sub("", base).lower() or base.lower()


def diff(old: dict | None, new: dict) -> dict:
    """Что изменилось от снимка old к new: моды добавлены / удалены / обновлены / включены-выключены, настройки."""
    a = (old or {}).get("files", {})
    b = new["files"]
    mods_a = {p[5:]: m for p, m in a.items() if p.startswith("mods/") and "/" not in p[5:]}
    mods_b = {p[5:]: m for p, m in b.items() if p.startswith("mods/") and "/" not in p[5:]}
    gone = {n: m for n, m in mods_a.items() if n not in mods_b}
    came = {n: m for n, m in mods_b.items() if n not in mods_a}
    toggled, updated = [], []
    for n in list(came):
        twin = n[:-9] if n.endswith(".disabled") else n + ".disabled"
        if twin in gone and gone[twin]["sha1"] == came[n]["sha1"]:
            toggled.append({"name": n.removesuffix(".disabled"), "on": not n.endswith(".disabled")})
            del gone[twin], came[n]
    by_key = {}
    for n in gone:
        by_key.setdefault(_mod_key(n), []).append(n)
    for n in list(came):
        olds = by_key.get(_mod_key(n))
        if olds:
            o = olds.pop(0)
            updated.append({"from": o.removesuffix(".disabled"), "to": n.removesuffix(".disabled")})
            del gone[o], came[n]
    other = [p for p in set(a) | set(b) if not p.startswith("mods/") or "/" in p[5:]]
    changed = sorted(p for p in other if a.get(p, {}).get("sha1") != b.get(p, {}).get("sha1"))
    return {"added": sorted(n.removesuffix(".disabled") for n in came),
            "removed": sorted(n.removesuffix(".disabled") for n in gone),
            "updated": updated, "toggled": toggled,
            "settings": len(changed), "settings_files": changed[:30]}


def history(inst_root: Path) -> list[dict]:
    """Снимки (новые сверху) и что в каждом изменилось по сравнению с предыдущим."""
    snaps = _manifests(_dir(inst_root))
    out = []
    for i, s in enumerate(snaps):
        out.append({**_summary(s), "changes": diff(snaps[i - 1] if i else None, s), "first": i == 0})
    return list(reversed(out))


def pending_changes(inst_root: Path, game_dir: Path) -> dict | None:
    """Что поменялось в сборке с последнего снимка (для подсказки «есть несохранённые изменения»)."""
    store = _dir(inst_root)
    snaps = _manifests(store)
    if not snaps:
        return None
    store.mkdir(parents=True, exist_ok=True)
    return diff(snaps[-1], {"files": _scan(game_dir, store)})


def restore(inst_root: Path, game_dir: Path, snap_id: str) -> dict:
    """Возвращает сборку к снимку. Сначала сохраняет текущее состояние (откат тоже можно отменить).
    Файлы вне отслеживаемых папок (миры, логи, скриншоты) не трогаются."""
    store = _dir(inst_root)
    target = _read_json(store / f"{Path(str(snap_id)).name}.json", None)
    if not isinstance(target, dict) or "files" not in target:
        raise ValueError("Этот снимок уже удалён")
    blobs = store / "blobs"
    missing = [rel for rel, m in target["files"].items() if not (blobs / m["sha1"]).is_file()]
    if missing:
        raise ValueError(f"Снимок повреждён: нет {len(missing)} файл(ов), например {missing[0]}")
    before = create(inst_root, game_dir, "Перед возвратом к точке", keep=KEEP + 1)
    root = game_dir.resolve()
    want = target["files"]
    for p in _tracked(game_dir):
        rel = p.relative_to(game_dir).as_posix()
        if rel not in want:
            p.unlink()
    for rel, meta in want.items():
        dest = (game_dir / rel).resolve()
        if root not in dest.parents:
            continue                                    # защита от путей вне сборки
        if dest.is_file() and dest.stat().st_size == meta["size"] and _sha1(dest) == meta["sha1"]:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".vaultrestore")
        shutil.copyfile(blobs / meta["sha1"], tmp)
        os.replace(tmp, dest)
    for d in TRACKED_DIRS:                              # пустые папки, оставшиеся от удалённых файлов
        base = game_dir / d
        if base.is_dir():
            for sub in sorted((x for x in base.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
                try:
                    sub.rmdir()
                except OSError:
                    pass
    _prune(store, KEEP)
    return {"restored": target["id"], "before": before["id"]}


def delete(inst_root: Path, snap_id: str) -> None:
    store = _dir(inst_root)
    (store / f"{Path(str(snap_id)).name}.json").unlink(missing_ok=True)
    _prune(store, KEEP)
