"""
Проверка модов до запуска: читаем описания модов внутри .jar и ищем то, что точно уронит игру.

Что ловим:
  • мод не под тот загрузчик (Fabric-мод в сборке Forge и наоборот);
  • мод под другую версию игры;
  • нет обязательной зависимости (например, Fabric API или Architectury);
  • один и тот же мод лежит дважды (две версии).

Описания читаем из fabric.mod.json / quilt.mod.json / META-INF/mods.toml / META-INF/neoforge.mods.toml.
Результат кэшируется по имени, размеру и дате файла — повторная проверка почти мгновенная.
"""
from __future__ import annotations

import io
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

CACHE_NAME = ".vaultmc-modcheck.json"
CACHE_VERSION = 4
# id, которые даёт сама игра или загрузчик — их не ищем среди модов
BUILTIN = {"minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader", "forge", "neoforge",
           "mixinextras", "fml", "javafml", "lowcodefml"}


@dataclass
class ModInfo:
    file: str
    kind: str                         # fabric | quilt | forge | neoforge | unknown
    ids: list[str] = field(default_factory=list)          # id мода и всё, что он «предоставляет»
    name: str = ""
    depends: dict[str, str] = field(default_factory=dict)  # обязательные зависимости: id → диапазон версий
    minecraft: str = ""               # требуемая версия игры (как написано в моде)
    kinds: list[str] = field(default_factory=list)        # все загрузчики, под которые в файле есть описание
    forms: list[dict] = field(default_factory=list)       # описания под каждый загрузчик (у «универсальных» модов)
    version: str = ""                 # версия самого мода (если указана)
    breaks: dict[str, str] = field(default_factory=dict)      # моды, с которыми этот мод точно не работает: id → версии
    conflicts: dict[str, str] = field(default_factory=dict)   # моды, с которыми возможны проблемы (предупреждение автора)


# ------------------------------------------------------------------ чтение описаний модов
def _fabric_like(data: dict, kind: str, file: str, nested_ids: list[str]) -> ModInfo:
    info = ModInfo(file, kind)
    if kind == "quilt":
        q = data.get("quilt_loader", {})
        info.ids = [str(q.get("id", ""))] + [str(p if isinstance(p, str) else p.get("id", "")) for p in q.get("provides", [])]
        info.name = str(q.get("metadata", {}).get("name") or q.get("id") or "")
        for d in q.get("depends", []):
            if isinstance(d, str):
                info.depends[d] = "*"
            elif isinstance(d, dict) and not d.get("optional"):
                info.depends[str(d.get("id", ""))] = _join(d.get("versions", "*"))
    else:
        info.ids = [str(data.get("id", ""))] + [str(p) for p in data.get("provides", [])]
        info.name = str(data.get("name") or data.get("id") or "")
        for dep, rng in (data.get("depends") or {}).items():
            info.depends[str(dep)] = _join(rng)
    info.ids += nested_ids
    info.minecraft = info.depends.pop("minecraft", "")
    if kind == "quilt":
        info.version = str(data.get("quilt_loader", {}).get("version", "") or "")
    else:
        info.version = str(data.get("version", "") or "")
        for key, target in (("breaks", info.breaks), ("conflicts", info.conflicts)):
            for dep, rng in (data.get(key) or {}).items():
                target[str(dep)] = _join(rng)
    return info


def _forge_like(text: str, kind: str, file: str) -> ModInfo:
    info = ModInfo(file, kind)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return info
    mods = data.get("mods") or []
    info.ids = [str(m.get("modId", "")) for m in mods if isinstance(m, dict)]
    info.name = str(mods[0].get("displayName") or info.ids[0]) if mods and info.ids else ""
    if mods and isinstance(mods[0], dict):
        ver = str(mods[0].get("version", "") or "")
        info.version = "" if ver.startswith("${") else ver      # «${file.jarVersion}» — версия подставляется при сборке
    for owner, deps in (data.get("dependencies") or {}).items():
        for d in deps if isinstance(deps, list) else []:
            if not isinstance(d, dict):
                continue
            kind_dep = str(d.get("type", "")).lower()
            required = d.get("mandatory") is True or kind_dep == "required"
            dep_id = str(d.get("modId", ""))
            if dep_id and kind_dep == "incompatible":        # NeoForge: «этот мод не работает вместе с …»
                info.breaks[dep_id] = str(d.get("versionRange", "*"))
            elif dep_id and kind_dep == "discouraged":
                info.conflicts[dep_id] = str(d.get("versionRange", "*"))
            if dep_id == "neoforge" and kind == "forge":
                info.kind = "neoforge"      # NeoForge 1.20.2–1.20.4 ещё пишет mods.toml, но зависит от neoforge
            if str(d.get("side", "BOTH")).upper() == "SERVER":
                continue                    # нужна только на сервере — игре у игрока не мешает
            if dep_id == "minecraft":
                info.minecraft = str(d.get("versionRange", ""))
            elif required and dep_id:
                info.depends[dep_id] = str(d.get("versionRange", "*"))
    return info


def _join(rng) -> str:
    if isinstance(rng, list):
        return " || ".join(str(r) for r in rng)
    return str(rng)


def read_mod(path: Path) -> ModInfo:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            nested = []
            for n in names:   # вложенные моды (Fabric API состоит из десятков модулей внутри)
                if n.startswith("META-INF/jars/") and n.endswith(".jar"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(n))) as inner:
                            if "fabric.mod.json" in inner.namelist():
                                d = json.loads(inner.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
                                nested.append(str(d.get("id", "")))
                                nested += [str(p) for p in d.get("provides", [])]
                    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError):
                        pass
                if n.startswith("META-INF/jarjar/") and n.endswith(".jar"):   # Forge/NeoForge: библиотеки внутри мода
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(n))) as inner:
                            for toml in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                                if toml in inner.namelist():
                                    nested += _forge_like(inner.read(toml).decode("utf-8", "replace"), "forge", n).ids
                                    break
                    except (zipfile.BadZipFile, KeyError, OSError):
                        pass
            found: list[ModInfo] = []      # один файл может быть сразу под несколько загрузчиков
            if "quilt.mod.json" in names:
                found.append(_fabric_like(json.loads(z.read("quilt.mod.json").decode("utf-8", "replace"), strict=False), "quilt", path.name, nested))
            if "fabric.mod.json" in names:
                found.append(_fabric_like(json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"), strict=False), "fabric", path.name, nested))
            if "META-INF/neoforge.mods.toml" in names:
                found.append(_forge_like(z.read("META-INF/neoforge.mods.toml").decode("utf-8", "replace"), "neoforge", path.name))
            if "META-INF/mods.toml" in names:
                found.append(_forge_like(z.read("META-INF/mods.toml").decode("utf-8", "replace"), "forge", path.name))
            if found:
                for f in found:
                    if f.kind not in ("fabric", "quilt"):   # у Fabric/Quilt вложенные уже учтены
                        f.ids += nested
                main = found[0]
                main.kinds = list(dict.fromkeys(f.kind for f in found))
                if len(found) > 1:
                    main.forms = [{"kind": f.kind, "ids": f.ids, "name": f.name, "depends": f.depends,
                                   "minecraft": f.minecraft, "version": f.version, "breaks": f.breaks,
                                   "conflicts": f.conflicts} for f in found]
                return main
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, KeyError, UnicodeDecodeError):
        pass
    return ModInfo(path.name, "unknown")


# ------------------------------------------------------------------ версии
def _vt(v: str) -> tuple:
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def version_matches(game: str, rng: str) -> bool | None:
    """Подходит ли версия игры под требование мода. None — не смогли понять запись (тогда не мешаем)."""
    rng = (rng or "").strip()
    if not rng or rng == "*":
        return True
    g = _vt(game)
    # Maven-диапазоны Forge: [1.20,1.21)  [1.20.1]  (,1.21)  — можно через запятую несколько
    if rng[0] in "[(":
        ok_any = False
        for part in re.findall(r"[\[(][^\])]*[\])]", rng):
            lo_inc, hi_inc = part[0] == "[", part[-1] == "]"
            body = part[1:-1]
            if "," not in body:
                ok_any |= g == _vt(body)
                continue
            lo, hi = (s.strip() for s in body.split(",", 1))
            ok = True
            if lo:
                ok &= g >= _vt(lo) if lo_inc else g > _vt(lo)
            if hi:
                ok &= g <= _vt(hi) if hi_inc else g < _vt(hi)
            ok_any |= ok
        return ok_any
    # Fabric: ">=1.20 <1.21", "~1.20.1", "1.20.x", "1.20.1", "1.20.1 || 1.20.2"
    results = []
    for alt in rng.split("||"):
        alt = alt.strip()
        if not alt:
            continue
        ok = True
        for cond in alt.split():
            m = re.match(r"^(>=|<=|>|<|=|~|\^)?\s*([\w.\-+*]+)$", cond)
            if not m:
                return None
            op, ver = m.group(1) or "=", m.group(2)
            if re.search(r"[a-z]", ver.split("-")[0].replace("x", ""), re.I) and not ver.endswith((".x", ".*")):
                return None   # снапшоты и прочее — не судим
            if "-" in ver and re.search(r"[a-z]", ver.split("-", 1)[1], re.I):
                return None   # «1.21-pre1», «1.20.1-snapshot», «1.21-rc1» — пред-версии тоже не судим
            if ver.endswith((".x", ".*")) or ver in ("x", "*"):
                prefix = _vt(ver.rstrip(".x*").rstrip(".")) if ver not in ("x", "*") else ()
                n = len(re.findall(r"\d+", ver))
                ok &= g[:n] == prefix[:n]
                continue
            v = _vt(ver)
            n = len(re.findall(r"\d+", ver.split("-")[0]))
            if op == "=":
                ok &= g[:max(n, 2)] == v[:max(n, 2)] if n < 3 else g == v
            elif op == ">=":
                ok &= g >= v
            elif op == ">":
                ok &= g > v
            elif op == "<=":
                ok &= g <= v
            elif op == "<":
                ok &= g < v
            elif op == "~":
                ok &= g >= v and g[:2] == v[:2]
            elif op == "^":
                ok &= g >= v and g[:1] == v[:1]
        results.append(ok)
    return any(results) if results else None


# ------------------------------------------------------------------ проверка сборки
def _scan(mods_dir: Path) -> list[ModInfo]:
    cache_path = mods_dir / CACHE_NAME
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache.get("v") != CACHE_VERSION:
            cache = {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    entries = cache.get("mods", {})
    out, fresh = [], {}
    for f in sorted(mods_dir.iterdir()) if mods_dir.is_dir() else []:
        if not f.is_file() or f.suffix.lower() != ".jar":
            continue
        st = f.stat()
        key = f"{f.name}|{st.st_size}|{int(st.st_mtime)}"
        hit = entries.get(key)
        info = ModInfo(**hit) if isinstance(hit, dict) else read_mod(f)
        fresh[key] = info.__dict__
        out.append(info)
    try:
        cache_path.write_text(json.dumps({"v": CACHE_VERSION, "mods": fresh}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def _pick_form(m: "ModInfo", loader: str, game_version: str, all_ids: set[str]) -> "ModInfo":
    """У мода «под несколько загрузчиков» берём описание под загрузчик сборки (его зависимости и версию игры)."""
    if not m.forms:
        return m
    best = next((f for f in m.forms if f["kind"] == loader), None) \
        or next((f for f in m.forms if _loader_ok(f["kind"], loader, game_version, all_ids)), None) or m.forms[0]
    return ModInfo(m.file, best["kind"], list(best["ids"]), best["name"], dict(best["depends"]), best["minecraft"],
                   kinds=m.kinds, forms=m.forms, version=best.get("version", ""),
                   breaks=dict(best.get("breaks", {})), conflicts=dict(best.get("conflicts", {})))


def _loader_ok_any(m: "ModInfo", loader: str, game_version: str, all_ids: set[str]) -> bool:
    return any(_loader_ok(k, loader, game_version, all_ids) for k in (m.kinds or [m.kind]))


def _loader_ok(mod_kind: str, loader: str, game_version: str, all_ids: set[str]) -> bool:
    if mod_kind == "unknown":
        return True
    if loader in ("fabric", "quilt"):
        return mod_kind in ("fabric", "quilt") and not (loader == "fabric" and mod_kind == "quilt")
    if loader == "forge":
        return mod_kind == "forge" or (mod_kind == "fabric" and "connector" in all_ids)
    if loader == "neoforge":
        # NeoForge 1.20.1 ещё понимает моды Forge; Fabric-моды — только через Sinytra Connector
        return mod_kind == "neoforge" or (mod_kind == "forge" and _vt(game_version)[:3] <= (1, 20, 1)) \
            or (mod_kind == "fabric" and "connector" in all_ids)
    return True


LOADER_TITLES = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge"}


def _incompatible(mods: list["ModInfo"], wrong_files: set[str], deep: bool) -> list[dict]:
    """Моды, которые сами заявляют «со мной не работает X» (breaks / conflicts / incompatible) — и X стоит в сборке."""
    out: list[dict] = []
    by_id: dict[str, list[ModInfo]] = {}
    for m in mods:
        if m.file not in wrong_files:
            for i in m.ids:
                if i:
                    by_id.setdefault(i, []).append(m)
    seen: set[tuple[str, str]] = set()
    for m in mods:
        if m.file in wrong_files:
            continue
        for hard, table in ((True, m.breaks), (False, m.conflicts)):
            if not hard and not deep:
                continue
            for other_id, rng in table.items():
                for o in by_id.get(other_id, []):
                    if o.file == m.file or (m.file, o.file) in seen:
                        continue
                    if rng.strip() not in ("", "*") and not (o.version and version_matches(o.version, rng)):
                        continue            # версия другого мода не попадает под запрет (или неизвестна)
                    seen.add((m.file, o.file))
                    a, b = m.name or m.file, o.name or o.file
                    what = "точно не работает вместе с" if hard else "может плохо работать вместе с"
                    out.append({"id": f"{'breaks' if hard else 'conflict'}:{m.file}:{o.file}", "level": "error" if hard else "warn",
                                "title": f"«{a}» и «{b}» не уживаются",
                                "text": f"Автор мода «{a}» указал, что он {what} «{b}»"
                                        f"{' (версии ' + rng.replace('||', 'или') + ')' if rng.strip() not in ('', '*') else ''}. "
                                        "Оставьте один из них или обновите оба.",
                                "file": o.file, "fix": {"action": "disable_mod", "arg": o.file, "label": f"Выключить {o.file}"},
                                "alt": {"action": "disable_mod", "arg": m.file, "label": f"Выключить {m.file}"}})
    return out


def check_mods(mods_dir: Path, loader: str, game_version: str, deep: bool = False) -> list[dict]:
    """Проблемы, из-за которых игра почти наверняка не запустится. Каждая: {id, title, text, file, fix}.
    deep=True (для «Починки сборки») добавляет ещё и предупреждения авторов модов о возможных конфликтах."""
    if loader not in LOADER_TITLES:
        return []
    mods = _scan(mods_dir)
    all_ids = {i for m in mods for f in (m.forms or [m.__dict__]) for i in f["ids"] if i}
    mods = [_pick_form(m, loader, game_version, all_ids) for m in mods]
    issues: list[dict] = []
    wrong_files: set[str] = set()

    for m in mods:
        if not _loader_ok_any(m, loader, game_version, all_ids):
            wrong_files.add(m.file)
            issues.append({"id": f"loader:{m.file}", "title": f"«{m.name or m.file}» — мод для {LOADER_TITLES.get(m.kind, m.kind)}",
                           "text": f"Сборка на {LOADER_TITLES[loader]}, а этот мод сделан для другого загрузчика — игра с ним не запустится.",
                           "file": m.file, "fix": {"action": "disable_mod", "arg": m.file, "label": "Выключить"}})
            continue
        if m.minecraft and version_matches(game_version, m.minecraft) is False:
            wrong_files.add(m.file)
            issues.append({"id": f"version:{m.file}", "title": f"«{m.name or m.file}» — не для {game_version}",
                           "text": f"Мод требует Minecraft {m.minecraft.replace('||', 'или')}. Нужна его версия под {game_version} "
                                   "— найдите её в каталоге или выключите мод.",
                           "file": m.file, "fix": {"action": "disable_mod", "arg": m.file, "label": "Выключить"}})

    issues += _incompatible(mods, wrong_files, deep)

    # одинаковые моды (две версии одного и того же)
    seen: dict[str, str] = {}
    for m in mods:
        main = m.ids[0] if m.ids and m.ids[0] else ""
        if not main or m.file in wrong_files:
            continue
        if main in seen:
            # выключить предлагаем более старую версию (по номеру в имени файла)
            older = min((seen[main], m.file), key=lambda f: tuple(int(x) for x in re.findall(r"\d+", f)[:6]))
            issues.append({"id": f"dup:{main}", "title": f"«{m.name or main}» лежит дважды",
                           "text": f"В папке модов две версии одного мода: {seen[main]} и {m.file}. Оставьте одну — "
                                   f"лаунчер предлагает убрать более старую.",
                           "file": older, "fix": {"action": "disable_mod", "arg": older, "label": f"Выключить {older}"}})
        else:
            seen[main] = m.file

    # обязательные зависимости
    usable = {i for m in mods if m.file not in wrong_files for i in m.ids if i}
    missing: dict[str, list[str]] = {}
    for m in mods:
        if m.file in wrong_files:
            continue
        for dep in m.depends:
            d = dep.strip()
            if not d or d in BUILTIN or d in usable or d.startswith(("fabric-", "fabric_")) and "fabric-api" in usable:
                continue
            missing.setdefault(d, []).append(m.name or m.file)
    for dep, who in missing.items():
        issues.append({"id": f"dep:{dep}", "title": f"Не хватает мода «{dep}»",
                       "text": f"Он нужен для: {', '.join(who[:3])}{' и др.' if len(who) > 3 else ''}. Без него игра не запустится.",
                       "file": "", "fix": {"action": "install_dep", "arg": dep, "label": "Установить"}})
    return issues
