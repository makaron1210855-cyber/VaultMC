"""
«Починка сборки»: ищет причины, из-за которых игра не запускается или показывает чёрный экран, и чинит их в один клик.

Что проверяется (всё локально, без интернета):
  1. моды: не тот загрузчик / версия игры, нет зависимостей, дубли (core/modcheck.py);
  2. моды, которые сами заявляют «со мной не работает X» (breaks / conflicts / incompatible) — и X стоит в сборке;
  3. известные «дубли по смыслу» (например, два мода динамического света; Sodium вместе с Embeddium);
  4. моды, в названии файла которых другая версия игры (mc1.17 в сборке 1.20.1);
  5. повреждённые .jar (не открываются как архив);
  6. шейдеры Iris/Oculus включены — самая частая причина чёрного экрана;
  7. ресурспаки из options.txt: битый pack.mcmeta (игра пишет «Missing pack_format»);
  8. последний журнал игры и отчёт о вылете: известные причины (core/crash.py), ошибки шейдеров, перезапись одних и тех же методов.

Каждая находка: {id, level: error|warn|info, title, text, fix: {action, arg, label} | None, alt: {...} | None}.
Все исправления обратимы: моды переименовываются в .jar.disabled, options.txt сохраняется в options.txt.vaultmc.bak.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import crash
from .modcheck import check_mods

SHADER_MODS = ("iris", "oculus", "optifine")

# группы модов, которые делают одно и то же: вместе они чаще всего ломают игру. Первый в списке — «главный».
GROUPS = (
    ("Два мода динамического света", ("lambdynamiclights", "dynamiclights", "ryoamiclights", "sodiumdynamiclights"),
     "Эти моды делают одно и то же (свет от предметов в руке). Вместе они могут давать чёрные блоки и вылеты.", False),
    ("Sodium вместе с Embeddium/Rubidium", ("sodium", "embeddium", "rubidium"),
     "Это три разных мода одного назначения (ускорение отрисовки) — работает только один из них.", False),
    ("VulkanMod вместе с Sodium/Iris", ("vulkanmod", "sodium", "embeddium", "iris", "oculus"),
     "VulkanMod заменяет отрисовку игры целиком, поэтому он несовместим с Sodium, Iris и им подобными — получается чёрный экран.", True),
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _jars(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return sorted(f for f in mods_dir.iterdir() if f.is_file() and f.suffix.lower() == ".jar")


def _starts(f: Path, names: tuple[str, ...]) -> str:
    stem = _norm(f.stem)
    return next((n for n in names if stem.startswith(_norm(n))), "")


# ------------------------------------------------------------------ отдельные проверки
def _group_issues(jars: list[Path]) -> list[dict]:
    out = []
    for title, names, text, anchor in GROUPS:
        found: dict[str, Path] = {}
        for f in jars:
            n = _starts(f, names)
            if n and n not in found:
                found[n] = f
        # «sodium» в названии sodium-extra / reeses-sodium-options — это не сам Sodium: берём только файлы, где имя
        # начинается с группы и дальше идёт версия или конец
        strict = {}
        for n, f in found.items():
            rest = _norm(f.stem)[len(_norm(n)):]
            if rest[:1].isdigit() or rest[:1] == "" or rest.startswith(("fabric", "forge", "neoforge", "mc", "v")):
                strict[n] = f
        if len(strict) < 2 or (anchor and names[0] not in strict):
            continue
        if anchor:      # «главный» мод группы (VulkanMod) выключаем, остальные оставляем
            keep_name = next(n for n in names[1:] if n in strict)
            drop = [strict[names[0]]]
        else:
            keep_name = next(n for n in names if n in strict)
            drop = [f for n, f in strict.items() if n != keep_name]
        out.append({"id": "group:" + _norm(title), "level": "warn", "title": title,
                    "text": f"{text} Найдено: {', '.join(f.name for f in strict.values())}. Оставить предлагаю {strict[keep_name].name}.",
                    "fix": {"action": "disable_mod", "arg": drop[0].name,
                            "label": "Выключить " + ", ".join(f.name for f in drop)} if len(drop) == 1 else
                    {"action": "disable_mods", "arg": [f.name for f in drop], "label": "Выключить " + ", ".join(f.name for f in drop)},
                    "alt": None})
    return out


def _filename_version_issues(jars: list[Path], game_version: str, skip: set[str]) -> list[dict]:
    g = re.findall(r"\d+", game_version)[:2]
    if len(g) < 2:
        return []
    out = []
    for f in jars:
        if f.name in skip:
            continue
        # версия игры в названии файла: «mc1.17», «+1.20.4», «minecraft-1.20.0» — отдельные номера версий мода так не пишут
        found = re.findall(r"(?:mc|minecraft[-_]?|\+)(1\.\d{1,2})(?:\.\d+)?", f.stem, re.I)
        if not found or any(v.split(".") == g for v in found):
            continue
        out.append({"id": f"filever:{f.name}", "level": "warn", "title": f"«{f.name}» — похоже, не для {game_version}",
                    "text": f"В названии файла указана версия игры {found[0]}, а в сборке {game_version}. "
                            "Такой мод часто не работает или даёт чёрный экран. Найдите версию под вашу игру в каталоге.",
                    "fix": {"action": "disable_mod", "arg": f.name, "label": "Выключить"}, "alt": None})
    return out


def _broken_jars(jars: list[Path]) -> list[dict]:
    out = []
    for f in jars:
        try:
            ok = zipfile.is_zipfile(f) and f.stat().st_size > 0
        except OSError:
            ok = False
        if not ok:
            out.append({"id": f"broken:{f.name}", "level": "error", "title": f"«{f.name}» — повреждённый файл",
                        "text": "Файл не открывается как мод (скачался не до конца или испорчен). Игра с ним не запустится.",
                        "fix": {"action": "disable_mod", "arg": f.name, "label": "Выключить"}, "alt": None})
    return out


def _iris_file(game_dir: Path) -> Path:
    return game_dir / "config" / "iris.properties"


def _read_props(path: Path) -> dict[str, str]:
    props = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()
    except OSError:
        pass
    return props


def _shader_issue(game_dir: Path, jars: list[Path]) -> list[dict]:
    if not any(_starts(f, ("iris", "oculus")) for f in jars):
        return []
    props = _read_props(_iris_file(game_dir))
    pack = props.get("shaderPack", "")
    if props.get("enableShaders", "").lower() != "true" or not pack:
        return []
    return [{"id": "shaders_on", "level": "warn", "title": f"Включены шейдеры: «{pack}»",
             "text": "Шейдеры — самая частая причина чёрного экрана: пакет может не подходить к версии игры, видеокарте "
                     "или другому моду. Выключите шейдеры и запустите игру; если всё работает — включайте обратно в настройках игры.",
             "fix": {"action": "shaders_off", "arg": "", "label": "Выключить шейдеры"}, "alt": None}]


def _options_packs(game_dir: Path) -> list[str]:
    try:
        text = (game_dir / "options.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = re.search(r"^resourcePacks:(\[.*\])\s*$", text, re.M)
    try:
        return [str(x) for x in json.loads(m.group(1))] if m else []
    except json.JSONDecodeError:
        return []


def _pack_meta_error(path: Path) -> str:
    """Пусто — пакет в порядке (или это не наше дело); иначе — что с ним не так."""
    try:
        if path.is_dir():
            raw = (path / "pack.mcmeta").read_bytes() if (path / "pack.mcmeta").is_file() else None
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                raw = z.read("pack.mcmeta") if "pack.mcmeta" in z.namelist() else None
        else:
            return "файл не открывается как архив"
    except (OSError, zipfile.BadZipFile, KeyError):
        return "файл не читается"
    if raw is None:
        return ""      # без pack.mcmeta игра просто не покажет пакет в списке — не критично
    try:
        data = json.loads(raw.decode("utf-8-sig", "replace"), strict=False)
    except json.JSONDecodeError:
        return "pack.mcmeta повреждён"
    meta = data.get("pack") if isinstance(data, dict) else None
    if not isinstance(meta, dict) or not isinstance(meta.get("pack_format"), int):
        return "в pack.mcmeta нет числа pack_format"
    return ""


def _pack_issues(game_dir: Path) -> list[dict]:
    out = []
    for entry in _options_packs(game_dir):
        if not entry.startswith("file/"):
            continue
        name = entry[5:]
        path = game_dir / "resourcepacks" / name
        if not path.exists():
            continue
        err = _pack_meta_error(path)
        if err:
            out.append({"id": f"pack:{name}", "level": "error", "title": f"Ресурспак «{name}» с ошибкой",
                        "text": f"{err[0].upper() + err[1:]}. Игра пишет «Couldn't load pack metadata» и может не показать картинку. "
                                "Уберу его из включённых ресурспаков (сам файл останется в папке).",
                        "fix": {"action": "drop_pack", "arg": entry, "label": "Отключить ресурспак"}, "alt": None})
    return out


def _latest_log(game_dir: Path) -> str:
    try:
        return (game_dir / "logs" / "latest.log").read_text(encoding="utf-8", errors="replace")[-600_000:]
    except OSError:
        return ""


FIX_MARK = ".vaultmc_fixed_at"      # время последнего исправления: всё, что в журналах СТАРШЕ, уже «починено»
LOG_CLEAN_RE = re.compile(r"Stopping!")
LOG_CRASHED_RE = re.compile(r"#@!@# Game crashed|Crash report saved|Exception in thread|Incompatible mods found|"
                            r"Mod resolution failed|Failed to start|Could not create the Java Virtual Machine")


def mark_fixed(game_dir: Path) -> None:
    try:
        (game_dir / FIX_MARK).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def _fixed_at(game_dir: Path) -> float:
    try:
        return float((game_dir / FIX_MARK).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def _born(p: Path) -> float:
    """Когда файл создан: latest.log пересоздаётся при каждом запуске игры, то есть это начало последнего запуска."""
    st = p.stat()
    return getattr(st, "st_birthtime", None) or st.st_ctime


def _newest_report(game_dir: Path) -> str:
    folder = game_dir / "crash-reports"
    try:
        files = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        log = game_dir / "logs" / "latest.log"
        if not files:
            return ""
        newest = files[-1]
        # отчёт нужен только от ПОСЛЕДНЕГО запуска: он записан после того, как создан latest.log. Раньше хватало «не старше часа
        # до журнала» — и вылет, который уже давно починили удачным запуском, показывался как причина.
        if (not log.exists() or newest.stat().st_mtime >= _born(log) - 5) and newest.stat().st_mtime > _fixed_at(game_dir):
            return newest.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        pass
    return ""


OVERWRITE_RE = re.compile(r"Method overwrite conflict for \S+ in [\w\-.]+\.mixins\.json[^\n]*? from mod ([\w\-]+), "
                          r"previously written by ([\w.$]+)")
SHADER_ERR_RE = re.compile(r"(?:iris|oculus|shaderpack|shader)[^\n]{0,120}(?:Failed to compile|ShaderCompileException|"
                           r"compile error|Could not compile|link error|ProgramLinkException|exception)", re.I)


def _log_issues(game_dir: Path, mods_dir: Path, ram_gb: int, known: set[str]) -> list[dict]:
    text = _latest_log(game_dir)
    report = _newest_report(game_dir)
    log_file = game_dir / "logs" / "latest.log"
    try:
        # журнал старше последнего исправления (или изменения папки модов) описывает состояние ДО него: показывать из него
        # проблемы значит предлагать чинить уже починенное — после «Починить» находки возвращались бы те же самые
        if text and log_file.stat().st_mtime <= max(_fixed_at(game_dir), mods_dir.stat().st_mtime if mods_dir.is_dir() else 0.0):
            text = ""
    except OSError:
        pass
    if not text and not report:
        return []
    out: list[dict] = []
    # игра закрылась как обычно (в журнале «Stopping!», ни вылета, ни ошибки запуска) — это не вылет: слова вроде
    # «OutOfMemoryError» или «Mixin apply failed» в таком журнале — безобидные предупреждения, а не причина
    clean = bool(LOG_CLEAN_RE.search(text)) and not report and not LOG_CRASHED_RE.search(text)
    for f in ([] if clean else crash.analyze(text, report, mods_dir, ram_gb)):
        if f["title"].startswith("Причину не удалось"):
            continue
        key = "log:" + _norm(f["title"])
        if key in known:
            continue
        out.append({"id": key, "level": "error", "title": f["title"], "text": f["text"] + " (по журналу последнего запуска)",
                    "fix": f["fix"], "alt": None})
    if SHADER_ERR_RE.search(text) and (game_dir / "config" / "iris.properties").exists():
        out.append({"id": "log:shadererr", "level": "error", "title": "Ошибки шейдеров в журнале игры",
                    "text": "Игра пишет ошибки компиляции шейдеров — картинка при этом чёрная или пропадают блоки.",
                    "fix": {"action": "shaders_off", "arg": "", "label": "Выключить шейдеры"}, "alt": None})
    pairs: dict[tuple[str, str], int] = {}
    for mod, package in OVERWRITE_RE.findall(text):
        other = ""
        for token in package.split("."):      # имя мода обычно стоит в пути пакета: net.caffeinemc.mods.lithium.mixin...
            if len(token) < 4 or token.lower() in ("mixin", "mixins", "mods", "mod", "common", "client", "world", "impl",
                                                   "api", "net", "com", "org", "caffeinemc"):
                continue
            file = crash.find_mod_file(mods_dir, token)
            if file:
                other = file
                break
        key = (mod, other)
        pairs[key] = pairs.get(key, 0) + 1
    for (mod, other), n in list(pairs.items())[:4]:
        mod_file = crash.find_mod_file(mods_dir, mod)
        other_file = other if other.lower().endswith(".jar") else ""
        if not mod_file or not other_file:
            continue      # один из модов уже выключен или удалён: запись в журнале старая, чинить нечего

        def off(f):
            return {"action": "disable_mod", "arg": f, "label": "Выключить " + f} if f else None
        out.append({"id": f"overwrite:{mod}:{other}", "level": "info", "title": f"«{mod}» и «{other}» меняют одно и то же",
                    "text": f"В журнале игры {n} раз: оба мода переписывают один и тот же метод игры. Обычно это безвредно "
                            "(игра работает), но если картинка ломается — это первые подозреваемые. "
                            "Можно проверить: выключите один из них и запустите игру.",
                    "fix": off(other_file), "alt": off(mod_file)})
    return out


# ------------------------------------------------------------------ главное
MIGRATION_RE = re.compile(r"^[^\n]*(?:Legacy [^\n]*setup detected|migration (?:assistant|dialog)|skipping migration)[^\n]*$", re.M | re.I)


def _update_issues(game_dir: Path, log_text: str) -> list[dict]:
    """Недавно обновлённые моды (в архиве отката): если игра после обновления ведёт себя плохо — частая причина именно она.
    Если в журнале мод сам пишет про «миграцию со старой версии» — это ошибка, иначе просто подсказка про откат."""
    import time

    from .modupdates import _read_manifest
    try:
        entries = _read_manifest(game_dir)
    except Exception:  # noqa: BLE001
        return []
    lines = MIGRATION_RE.findall(log_text)
    try:
        log_time = (game_dir / "logs" / "latest.log").stat().st_mtime
    except OSError:
        log_time = 0.0
    out = []
    for e in entries:
        title = str(e.get("title", ""))
        if not title or time.time() - float(e.get("at", 0)) > 3 * 86400:
            continue
        # журнал считается только если он записан ПОСЛЕ обновления: иначе это запись о прошлой версии мода
        fresh = log_time >= float(e.get("at", 0)) - 5
        blamed = next((ln for ln in lines if fresh and _norm(title) and _norm(title) in _norm(ln)), "")
        fix = {"action": "rollback_mod", "arg": str(e.get("id")), "label": f"Вернуть {e.get('old_file', 'старую версию')}"}
        if blamed:
            out.append({"id": f"update-bad:{e.get('id')}", "level": "error",
                        "title": f"«{title}» после обновления не подходит к вашим настройкам",
                        "text": f"Лаунчер обновил мод {e.get('old_file')} → {e.get('new_file')}. В журнале игры новая версия пишет, "
                                f"что нашла настройки старой и хочет их «мигрировать» (строка: {blamed.strip()[:140]}), но без окна "
                                "для диалога у неё это не получается — обычно итог чёрный экран. Верните прежнюю версию: "
                                "она работала с вашими настройками.",
                        "fix": fix, "alt": None})
        else:
            out.append({"id": f"update:{e.get('id')}", "level": "info", "title": f"«{title}» недавно обновлён",
                        "text": f"{e.get('old_file')} → {e.get('new_file')}. Если проблема началась после обновления, верните старую версию.",
                        "fix": fix, "alt": None})
    return out


VIDEO_MODS = ("watermedia", "fancymenu", "reimaginedintro", "reimagined-intro", "drippyloadingscreen")


def _compat_issue(jars: list[Path], compat: bool) -> list[dict]:
    """Режим совместимости: запуск без оптимизаций лаунчера (флаги GC, кэш классов, приоритет, --fullscreen)."""
    if compat:
        return [{"id": "compat_on", "level": "info", "title": "Включён режим совместимости",
                 "text": "Сейчас игра запускается без оптимизаций лаунчера, как в обычных лаунчерах. Если проблема не ушла — "
                         "причина не в них; если игра стала работать, оставьте так.",
                 "fix": {"action": "compat_off", "arg": "", "label": "Выключить режим совместимости"}, "alt": None}]
    video = sorted({n for f in jars for n in VIDEO_MODS if _norm(n) in _norm(f.stem)})
    if not video:
        return []
    return [{"id": "compat_hint", "level": "warn", "title": "Видео-моды и особые флаги запуска",
             "text": f"В сборке есть моды с видео и нативным кодом ({', '.join(video)}). Они могут плохо дружить с настройками, "
                     "которыми лаунчер ускоряет игру (сборщик мусора ZGC, запрет явной сборки мусора, смена приоритета, запуск "
                     "в полный экран), — один из признаков: после заставки чёрный экран, а в другом лаунчере всё работает. "
                     "Предположение: попробуйте запуск в режиме совместимости (без этих настроек).",
             "fix": {"action": "compat_on", "arg": "", "label": "Включить режим совместимости"}, "alt": None}]


def scan(game_dir: Path, mods_dir: Path, loader: str, game_version: str, ram_gb: int = 4,
         compat: bool = False) -> list[dict]:
    """Все найденные проблемы сборки, сначала самые серьёзные."""
    jars = _jars(mods_dir)
    items: list[dict] = []
    items += _broken_jars(jars)
    broken = {i["fix"]["arg"] for i in items}
    if loader in ("fabric", "quilt", "forge", "neoforge"):
        try:
            for i in check_mods(mods_dir, loader, game_version, deep=True):
                items.append({"level": "error", "alt": None, **i})
        except Exception as exc:  # noqa: BLE001 — проверка не должна ломать остальные
            items.append({"id": "modcheck-failed", "level": "info", "title": "Проверку модов не удалось выполнить",
                          "text": str(exc), "fix": None, "alt": None})
        flagged = {i.get("file", "") for i in items} | broken
        items += _group_issues(jars)
        items += _filename_version_issues(jars, game_version, flagged)
    items += _shader_issue(game_dir, jars)
    items += _compat_issue(jars, compat)
    items += _update_issues(game_dir, _latest_log(game_dir))
    items += _pack_issues(game_dir)
    items += _log_issues(game_dir, mods_dir, ram_gb, {i["id"] for i in items})
    # одинаковые находки (одно исправление дважды) схлопываем
    seen, unique = set(), []
    for i in items:
        if i["id"] in seen:
            continue
        seen.add(i["id"])
        unique.append(i)
    order = {"error": 0, "warn": 1, "info": 2}
    unique.sort(key=lambda i: order.get(i["level"], 3))
    return unique


# ------------------------------------------------------------------ исправления
def _backup_options(game_dir: Path) -> None:
    src, dst = game_dir / "options.txt", game_dir / "options.txt.vaultmc.bak"
    if src.exists() and not dst.exists():       # первую (исходную) копию не затираем
        shutil.copy2(src, dst)


def shaders_off(game_dir: Path) -> str:
    """enableShaders=false в config/iris.properties (сам пакет шейдеров остаётся выбранным)."""
    path = _iris_file(game_dir)
    if not path.exists():
        return "Файла настроек шейдеров нет — выключать нечего"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    done = False
    for n, line in enumerate(lines):
        if line.strip().startswith("enableShaders"):
            lines[n] = "enableShaders=false"
            done = True
    if not done:
        lines.append("enableShaders=false")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "Шейдеры выключены — включить обратно можно в настройках игры"


def drop_pack(game_dir: Path, entry: str) -> str:
    """Убирает ресурспак из списка включённых в options.txt."""
    path = game_dir / "options.txt"
    text = path.read_text(encoding="utf-8", errors="replace")
    packs = _options_packs(game_dir)
    if entry not in packs:
        return "Этот ресурспак уже выключен"
    _backup_options(game_dir)
    packs.remove(entry)
    new = re.sub(r"^resourcePacks:\[.*\]\s*$", "resourcePacks:" + json.dumps(packs, ensure_ascii=False), text,
                 count=1, flags=re.M)
    path.write_text(new, encoding="utf-8")
    return "Ресурспак отключён (копия options.txt — options.txt.vaultmc.bak)"


def reset_visuals(game_dir: Path) -> str:
    """Чистая картинка: шейдеры выключены, ресурспаки пользователя отключены (встроенные остаются)."""
    notes = []
    if _iris_file(game_dir).exists():
        shaders_off(game_dir)
        notes.append("шейдеры выключены")
    packs = _options_packs(game_dir)
    keep = [p for p in packs if not p.startswith("file/")]
    if len(keep) != len(packs):
        _backup_options(game_dir)
        path = game_dir / "options.txt"
        text = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(re.sub(r"^resourcePacks:\[.*\]\s*$", "resourcePacks:" + json.dumps(keep, ensure_ascii=False), text,
                               count=1, flags=re.M), encoding="utf-8")
        notes.append(f"ресурспаков отключено: {len(packs) - len(keep)}")
    return ("Готово: " + ", ".join(notes)) if notes else "Шейдеров и ресурспаков в сборке нет — сбрасывать нечего"


# ------------------------------------------------------------------ «что лучше выключить»
def _mod_facts(path: Path) -> dict:
    """Что известно о моде из самого файла: описание, версия, сколько классов он встраивает в игру (mixin)."""
    desc, name, version, mixins = "", path.name, "", 0
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            mixins = sum(1 for n in names if n.endswith(".class") and not n.startswith("META-INF/")
                         and re.search(r"/mixins?/", n, re.I))
            if "fabric.mod.json" in names:
                d = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
                desc = str(d.get("description", "") or "")
                name = str(d.get("name") or d.get("id") or name)
                version = str(d.get("version", "") or "")
            elif "META-INF/neoforge.mods.toml" in names or "META-INF/mods.toml" in names:
                import tomllib
                raw = z.read("META-INF/neoforge.mods.toml" if "META-INF/neoforge.mods.toml" in names else "META-INF/mods.toml")
                mods = tomllib.loads(raw.decode("utf-8", "replace")).get("mods") or [{}]
                if mods and isinstance(mods[0], dict):
                    desc = str(mods[0].get("description", "") or "")
                    name = str(mods[0].get("displayName") or name)
    except Exception:  # noqa: BLE001 — не получилось прочитать: сравним по остальному
        pass
    return {"file": path.name, "name": name, "version": "" if version.startswith("${") else version,
            "description": " ".join(desc.split())[:400], "mixins": mixins, "size_kb": round(path.stat().st_size / 1024)}


def compare(mods_dir: Path, file_a: str, file_b: str) -> dict:
    """Сравнение двух модов-«соперников»: кто что делает, от кого зависят другие моды и какой выключить с меньшими потерями."""
    from .modcheck import _scan
    a_path, b_path = mods_dir / Path(file_a).name, mods_dir / Path(file_b).name
    if not a_path.is_file() or not b_path.is_file():
        return {"error": "Один из модов уже убран — обновите проверку"}
    infos = {m.file: m for m in _scan(mods_dir)}
    sides = []
    for path in (a_path, b_path):
        facts = _mod_facts(path)
        me = infos.get(path.name)
        ids = {i for i in (me.ids if me else []) if i}
        facts["dependents"] = sorted({(o.name or o.file) for f, o in infos.items()
                                      if f != path.name and ids & set(o.depends)})[:8]
        sides.append(facts)
    a, b = sides
    note = ("Это оценка по данным из самих модов, а не гарантия: проверьте игру после выключения. "
            "Выключенный мод всегда можно вернуть во вкладке «Моды».")
    if bool(a["dependents"]) != bool(b["dependents"]):
        keep, drop = (a, b) if a["dependents"] else (b, a)
        reason = (f"От «{keep['name']}» зависят другие моды ({', '.join(keep['dependents'][:3])}), "
                  f"а от «{drop['name']}» — никто.")
    elif a["dependents"] and b["dependents"]:
        return {"mods": sides, "disable": "", "reason": "От обоих модов зависят другие моды — выключать любой из них "
                "рискованно. Если картинка не ломается, оставьте как есть.", "note": ""}
    else:
        hi, lo = (a, b) if a["mixins"] >= b["mixins"] else (b, a)
        if not hi["mixins"] or lo["mixins"] >= hi["mixins"] * 0.85:
            return {"mods": sides, "disable": "", "note": "",
                    "reason": "Моды примерно равны по объёму правок игры, и другие моды от них не зависят. Прочитайте описания: "
                              "выключите тот, чьих функций вам нужно меньше, и запустите игру."}
        keep, drop = hi, lo
        reason = (f"«{keep['name']}» вносит больше правок в игру ({keep['mixins']} против {drop['mixins']}), "
                  f"значит делает больше — с «{drop['name']}» потеряется меньше.")
    return {"mods": sides, "disable": drop["file"], "keep": keep["file"], "reason": reason, "note": note}
