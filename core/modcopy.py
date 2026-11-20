"""Моды из другой сборки: докачать в готовую сборку то, чего в ней нет, и не сломать её.

plan()    — какие моды источника отсутствуют в сборке-получателе и можно ли их поставить:
            та же версия игры и загрузчик → файл копируется как есть; иначе ищем на Modrinth (по SHA-1 файла)
            версию мода под игру и загрузчик получателя; нет такой — мод не переносится (с причиной).
install() — ставит выбранные моды (их обязательные зависимости из той же сборки добавляются сами) через
            промежуточную папку и до перемещения в mods проверяет «сборка + новые моды» теми же правилами,
            что и проверка перед запуском (core/modcheck.py) и автоотключение конфликтов (core/conflicts.py).
            Мод, который принёс бы НОВУЮ проблему (не тот загрузчик/версия, дубль, «не уживается с», нет
            зависимости, известный конфликт вроде OptiFine + Sodium), не ставится — и игрок видит почему.
            Существующие моды сборки не трогаются никогда. Точку «История» перед переносом делает backend.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from . import conflicts
from .modcheck import ModInfo, _loader_ok_any, _pick_form, check_mods, read_mod, version_matches
from .modsources import MODRINTH_API, ModIndex, _compatible_loaders
from .net import download, post_json
from .modupdates import _sha1, _title

# моды, которые лаунчер ставит и убирает сам (комнаты, скины) — переносить их не нужно
SERVICE_PREFIXES = ("e4mc", "e4all", "mcwifipnp", "lanworldplugnplay", "customskinloader")
STAGE_DIR = ".vaultmc_modcopy"


def _norm(name: str) -> str:
    return conflicts._norm(name)


def _enabled_jars(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return sorted(f for f in mods_dir.iterdir() if f.is_file() and f.suffix.lower() == ".jar")


def _all_ids(mods_dir: Path) -> set[str]:
    """id всех модов получателя — и включённых, и выключенных (выключенный мод тоже «есть»: не дублируем)."""
    ids: set[str] = set()
    if not mods_dir.is_dir():
        return ids
    for f in mods_dir.iterdir():
        if f.is_file() and f.name.lower().endswith((".jar", ".jar.disabled")):
            ids.update(i for i in read_mod(f).ids if i)
    return ids


def _is_service(file: str) -> bool:
    n = _norm(file)
    return any(n.startswith(p) for p in SERVICE_PREFIXES)


def _main_id(m: ModInfo) -> str:
    return m.ids[0] if m.ids and m.ids[0] else ""


def _titles(game_dir: Path) -> dict[str, tuple[str, str, str]]:
    """Файл → (название, источник, id проекта) из индекса установленного лаунчером."""
    out = {}
    for key, e in ModIndex(game_dir).data.items():
        if isinstance(e, dict) and e.get("file") and ":" in key:
            source, pid = key.split(":", 1)
            out[Path(e["file"]).name] = (str(e.get("title") or ""), source, pid)
    return out


# ------------------------------------------------------------------ что можно перенести
def plan(src_game: Path, src_loader: str, src_game_version: str,
         dst_game: Path, dst_loader: str, dst_game_version: str, log=print) -> dict:
    src_mods, dst_mods = src_game / "mods", dst_game / "mods"
    have = _all_ids(dst_mods)
    titles = _titles(src_game)
    same = src_loader == dst_loader and src_game_version == dst_game_version
    cands: list[dict] = []
    already = 0
    for f in _enabled_jars(src_mods):
        if _is_service(f.name):
            continue
        raw = read_mod(f)
        m = _pick_form(raw, dst_loader, dst_game_version, have | set(raw.ids))
        main = _main_id(m)
        if main and main in have:
            already += 1
            continue
        title = _title(f, titles.get(f.name, ("", "", ""))[0] or None)   # название из .jar, а не «1.12.7-1.20.1 - Fabric»
        cands.append({"file": f.name, "id": main, "name": title, "version": m.version,
                      "depends": sorted(d for d in m.depends if d), "status": "", "reason": "", "_info": m,
                      "_known": m.kind != "unknown"})
    # совместим ли файл как есть
    for c in cands:
        m: ModInfo = c.pop("_info")
        known = c.pop("_known")
        # мод без описания загрузчика в другой загрузчик «как есть» не несём — ищем его версию на Modrinth
        fits = (known or src_loader == dst_loader) and _loader_ok_any(m, dst_loader, dst_game_version, have) \
            and version_matches(dst_game_version, m.minecraft) is not False
        if fits:                      # файл подходит как есть (в той же версии и загрузчике — почти всегда)
            c["status"] = "copy"
    # остальное — ищем версию под получателя на Modrinth
    need = [c for c in cands if not c["status"]]
    if need:
        by_sha = {_sha1(src_mods / c["file"]): c for c in need}
        found: dict = {}
        try:
            found = post_json(f"{MODRINTH_API}/version_files/update",
                              {"hashes": list(by_sha), "algorithm": "sha1",
                               "loaders": _compatible_loaders(dst_loader, dst_game_version),
                               "game_versions": [dst_game_version]}) or {}
        except Exception as exc:  # noqa: BLE001
            log(f"[Перенос модов] Modrinth недоступен: {exc}")
            for c in need:
                c["status"], c["reason"] = "no", "нет связи с Modrinth — версию под эту сборку найти не удалось"
        for sha, c in by_sha.items():
            if c["status"]:
                continue
            ver = found.get(sha) if isinstance(found, dict) else None
            files = (ver or {}).get("files") or []
            remote = next((x for x in files if x.get("primary")), None) or (files[0] if files else None)
            if not remote or not remote.get("url"):
                c["status"] = "no"
                c["reason"] = f"нет версии под {dst_loader.capitalize()} {dst_game_version}"
                continue
            c.update(status="download", url=remote["url"], newfile=remote.get("filename") or c["file"],
                     sha1=(remote.get("hashes") or {}).get("sha1", ""), size=int(remote.get("size") or 0),
                     newversion=str(ver.get("version_number") or ""), project=str(ver.get("project_id") or ""))
    cands.sort(key=lambda c: (c["status"] == "no", c["name"].lower()))
    return {"same": same, "already": already, "items": cands}


# ------------------------------------------------------------------ установка с проверкой
def _with_deps(chosen: list[dict], items: list[dict]) -> tuple[list[dict], list[str]]:
    """Добавляет к выбранным их обязательные зависимости, если они есть среди модов источника."""
    by_id = {i["id"]: i for i in items if i["id"] and i["status"] != "no"}
    picked = {c["file"]: c for c in chosen}
    added: list[str] = []
    queue = list(chosen)
    while queue:
        c = queue.pop()
        for dep in c.get("depends", []):
            d = by_id.get(dep)
            if d and d["file"] not in picked:
                picked[d["file"]] = d
                added.append(d["name"])
                queue.append(d)
    return list(picked.values()), added


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _known_conflict(staged: Path, others: list[Path]) -> str:
    """Правила conflicts.py: новый мод выключил бы существующий (или сам был бы выключен) — не ставим его."""
    for rule in conflicts.CONFLICTS:
        if conflicts._match([staged], rule.drop) and conflicts._match(others, rule.keep):
            return rule.reason
        if conflicts._match([staged], rule.keep) and conflicts._match(others, rule.drop):
            return rule.reason
    return ""


def _new_problems(dst_mods: Path, staged: dict[str, Path], loader: str, game_version: str, work: Path,
                  baseline: set[str]) -> dict[str, str]:
    """Новые проблемы «сборка + staged». Возвращает {файл из staged: причина} — какие моды не ставить."""
    check_dir = work / "check"
    shutil.rmtree(check_dir, ignore_errors=True)
    check_dir.mkdir(parents=True)
    for f in _enabled_jars(dst_mods):
        _link_or_copy(f, check_dir / f.name)
    for name, path in staged.items():
        _link_or_copy(path, check_dir / name)
    issues = check_mods(check_dir, loader, game_version)
    bad: dict[str, str] = {}
    infos = {name: _pick_form(read_mod(path), loader, game_version, set()) for name, path in staged.items()}
    for it in issues:
        if it["id"] in baseline:
            continue                                       # проблема была и до переноса — не наша
        reason = it["title"]
        files = [it.get("file") or "", ((it.get("alt") or {}).get("arg")) or ""]
        hit = next((f for f in files if f in staged), "")
        if hit:
            bad.setdefault(hit, reason)
        elif it["id"].startswith("dep:"):                  # не хватает зависимости — у каких новых модов
            dep = it["id"][4:]
            for name, info in infos.items():
                if dep in info.depends:
                    bad.setdefault(name, f"нужен мод «{dep}», а его нет ни в этой сборке, ни в той")
        elif it["id"].startswith("dup:"):                  # дубль: «старший» файл может оказаться и чужим
            main = it["id"][4:]
            for name, info in infos.items():
                if main in info.ids:
                    bad.setdefault(name, reason)
    shutil.rmtree(check_dir, ignore_errors=True)
    return bad


def install(src_game: Path, dst_game: Path, dst_loader: str, dst_game_version: str,
            items: list[dict], files: list[str], log=print) -> dict:
    dst_mods = dst_game / "mods"
    dst_mods.mkdir(parents=True, exist_ok=True)
    chosen = [i for i in items if i["file"] in set(files) and i["status"] in ("copy", "download")]
    chosen, added = _with_deps(chosen, items)
    work = dst_game / STAGE_DIR / uuid.uuid4().hex[:8]
    stage = work / "mods"
    stage.mkdir(parents=True, exist_ok=True)
    skipped: list[dict] = []
    staged: dict[str, Path] = {}
    meta: dict[str, dict] = {}
    try:
        for c in chosen:
            name = c.get("newfile") or c["file"]
            target = stage / name
            if (dst_mods / name).exists() or (dst_mods / (name + ".disabled")).exists():
                skipped.append({"name": c["name"], "reason": "файл с таким именем уже есть в сборке"})
                continue
            try:
                if c["status"] == "copy":
                    shutil.copy2(src_game / "mods" / c["file"], target)
                else:
                    download(c["url"], target, c.get("sha1") or None, c.get("size") or None)
            except Exception as exc:  # noqa: BLE001
                skipped.append({"name": c["name"], "reason": f"не скачался: {exc}"})
                continue
            staged[name], meta[name] = target, c
        # проверка: что сломалось бы, если положить эти моды в сборку
        baseline = {it["id"] for it in check_mods(dst_mods, dst_loader, dst_game_version)}
        for _ in range(6):                                 # убрали мод — пересматриваем остальные (зависимости от него)
            if not staged:
                break
            others = _enabled_jars(dst_mods) + [p for p in staged.values()]
            bad = {n: r for n, p in staged.items() if (r := _known_conflict(p, [o for o in others if o != p]))}
            if not bad:
                bad = _new_problems(dst_mods, staged, dst_loader, dst_game_version, work, baseline)
            if not bad:
                break
            for n, why in bad.items():
                skipped.append({"name": meta[n]["name"], "reason": why})
                staged.pop(n, None)
        installed: list[str] = []
        index = ModIndex(dst_game)
        src_titles = _titles(src_game)
        for name, path in staged.items():
            shutil.move(str(path), dst_mods / name)
            c = meta[name]
            installed.append(c["name"])
            title, source, pid = src_titles.get(c["file"], ("", "", ""))
            if c["status"] == "download" and c.get("project"):
                source, pid = "modrinth", c["project"]
            if source and pid:
                try:
                    index.add(source, pid, title or c["name"], name, "mods")
                except OSError:
                    pass
        log(f"[Перенос модов] поставлено {len(installed)}, не поставлено {len(skipped)}")
        return {"installed": installed, "skipped": skipped, "deps_added": added}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            (dst_game / STAGE_DIR).rmdir()
        except OSError:
            pass
