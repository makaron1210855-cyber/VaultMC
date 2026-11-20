"""«Лишние моды»: что в сборке реально бесполезно или мешает — по правилам, а не «на глаз».

Моды оптимизации часто ставят пачкой, и часть из них только мешает: два рендера вместе, два движка света, переходник
без самой программы, библиотека, которую никто не просит. Здесь только то, что можно доказать по самим файлам модов
(их id, версии, зависимости) и по известным несовместимостям. Каждая находка — с причиной простыми словами.
Ничего не удаляется: «убрать» = выключить мод (имя.jar.disabled), включить обратно можно на вкладке «Моды».

Правила (все они про мод целиком, а не про настройки):
  • два мода одной роли — Sodium/Embeddium/Rubidium/Xenon, Lithium/Radium/Canary, Starlight/Phosphor/ScalableLux:
    оставляем один (более новый и поддерживаемый), остальные выключаем;
  • Indium при Sodium 0.6+: Sodium сам умеет то, ради чего нужен был Indium;
  • OptiFabric без OptiFine: переходнику нечего подключать;
  • известные конфликты из core/conflicts.py (OptiFine с Sodium/Iris, Krypton с e4mc);
  • один и тот же мод в двух версиях;
  • библиотеки (Cloth Config, Architectury и т. п.), которые не требует ни один мод сборки.
Мод, от которого зависят другие, не предлагается (core/modsafe.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import conflicts
from .modcheck import ModInfo, _pick_form, _scan, check_mods
from .modsafe import removal_check
from .modupdates import _title

# Роли: от лучшего к худшему. Если в сборке есть несколько — остаётся первый из найденных.
# (ключи label / why — тексты для окна; по ним же работает перевод интерфейса, tools/i18n_scan.py)
ROLES = (
    {"label": "«рендер (Sodium и его ответвления)»", "ids": ("sodium", "embeddium", "rubidium", "xenon"),
     "why": "Это разные версии одного и того же ускорителя отрисовки — вместе они мешают друг другу."},
    {"label": "«оптимизация игровой логики (Lithium и его ответвления)»", "ids": ("lithium", "radium", "canary"),
     "why": "Это ответвления одного мода Lithium — вместе они дублируют друг друга."},
    {"label": "«движок освещения»", "ids": ("scalablelux", "starlight", "phosphor"),
     "why": "Два движка света переписывают одно и то же место игры — вместе освещение ломается."},
    {"label": "«динамическое освещение (свет от предмета в руке)»",
     "ids": ("lambdynlights", "dynamiclights", "ryoamiclights", "dynamic_lights"),
     "why": "Два мода делают одно и то же — светят от предмета в руке. Вместе они спорят, у кого свет главнее, и оба лишний раз нагружают игру."},
)

# Моды оптимизации (для подсчёта «сколько их у вас» и для правила про экспериментальные версии).
OPTIMIZATION_IDS = {
    "sodium", "embeddium", "rubidium", "xenon", "lithium", "radium", "canary", "ferritecore", "modernfix", "immediatelyfast",
    "entityculling", "moreculling", "cullleaves", "memoryleakfix", "badoptimizations", "smoothboot", "dynamic_fps", "dynamic-fps",
    "zfastnoise", "particle_core", "c2me", "vmp", "krypton", "scalablelux", "starlight", "phosphor", "sodium-extra", "indium",
    "fastquit", "lazydfu", "clumps", "enhancedblockentities", "noxesium", "saturn", "threadtuner", "fast-ip-ping",
}
# Оптимизаторы, которые автор сам называет экспериментальными (в версии есть alpha / beta / rc / snapshot).
EXPERIMENTAL_WORDS = ("alpha", "beta", "snapshot", "-rc", ".rc")

# Библиотеки без собственного содержимого: нужны, только если их требует другой мод.
LIBRARIES = {
    "cloth-config": "Cloth Config", "cloth_config": "Cloth Config", "architectury": "Architectury API",
    "fabric-language-kotlin": "Fabric Language Kotlin", "kotlinforforge": "Kotlin for Forge",
    "forgeconfigapiport": "Forge Config API Port", "puzzleslib": "Puzzles Lib", "collective": "Collective",
    "balm": "Balm", "bookshelf": "Bookshelf", "midnightlib": "MidnightLib", "owo": "oωo", "geckolib": "GeckoLib",
    "creativecore": "CreativeCore", "playeranimator": "Player Animator", "player-animator": "Player Animator",
    "supermartijn642corelib": "SuperMartijn642's Core Lib", "moonlight": "Moonlight Lib",
    "resourcefullib": "Resourceful Lib", "resourcefulconfig": "Resourceful Config", "yet_another_config_lib_v3": "YetAnotherConfigLib",
    "yacl": "YetAnotherConfigLib",
}


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def _title_of(f: Path, titles: dict) -> str:
    return _title(f, titles.get(f.name) or None)


def scan(mods_dir: Path, loader: str, game_version: str, titles: dict | None = None) -> list[dict]:
    """Список находок: {id, file, name, kind, why, keep}. Пусто — в сборке нет ничего лишнего."""
    titles = titles or {}
    if not mods_dir.is_dir():
        return []
    raw = _scan(mods_dir)
    all_ids = {i for m in raw for f in (m.forms or [m.__dict__]) for i in f["ids"] if i}
    mods = [_pick_form(m, loader, game_version, all_ids) for m in raw]
    by_file = {m.file: m for m in mods}
    path = {m.file: mods_dir / m.file for m in mods}
    found: dict[str, dict] = {}

    def name(f: str) -> str:
        return _title_of(path[f], titles) if f in path else f

    def add(file: str, kind: str, why: str, keep: str = "") -> None:
        if file in by_file and file not in found:
            found[file] = {"id": f"{kind}:{file}", "file": file, "name": name(file), "kind": kind, "why": why, "keep": keep,
                           "tier": "maybe" if kind == "maybe" else "sure"}

    def holders(ids) -> list[ModInfo]:
        return [m for m in mods if set(ids) & set(m.ids)]

    # --- два мода одной роли
    for role in ROLES:
        present = [(i, holders([i])) for i in role["ids"]]
        present = [(i, h) for i, h in present if h]
        if len(present) > 1:
            best_id, best = present[0]
            for other_id, others in present[1:]:
                for o in others:
                    if o.file == best[0].file:
                        continue
                    add(o.file, "twin", f"В сборке два мода одной роли — {role['label']}: «{name(best[0].file)}» и «{name(o.file)}». "
                                        f"{role['why']} Оставлен «{name(best[0].file)}».", best[0].file)

    # --- Indium при Sodium 0.6+
    sodium = holders(["sodium"])
    if sodium and any(_ver(s.version) >= (0, 6) for s in sodium):
        for ind in holders(["indium"]):
            add(ind.file, "obsolete", "Indium нужен был, чтобы моды могли менять отрисовку блоков поверх Sodium. В Sodium 0.6 и новее это "
                                      "уже встроено, Indium ничего не делает.", sodium[0].file)

    # --- OptiFabric без OptiFine
    has_optifine = any(re.sub(r"[^a-z0-9]", "", f.lower()).startswith("optifine") for f in path)
    if not has_optifine:
        for m in holders(["optifabric"]):
            add(m.file, "orphan", "OptiFabric — переходник, который подключает OptiFine к Fabric. Самого OptiFine в сборке нет, "
                                  "поэтому переходник бесполезен.")

    # --- известные конфликты (те же правила, что при запуске)
    jars = list(path.values())
    for rule in conflicts.CONFLICTS:
        keepers = conflicts._match(jars, rule.keep)
        if keepers:
            for f in conflicts._match(jars, rule.drop):
                add(f.name, "conflict", rule.reason + ".", keepers[0].name)

    # --- одинаковый мод в двух версиях
    for issue in check_mods(mods_dir, loader, game_version):
        if issue["id"].startswith("dup:") and issue.get("file"):
            add(issue["file"], "dup", "Этот мод лежит в сборке дважды (две версии). Лишняя — более старая.")

    # --- библиотеки, которые никто не просит
    gone = set(found)
    for _ in range(len(mods)):
        changed = False
        for m in mods:
            lib = m.ids[0] if m.ids and m.ids[0] in LIBRARIES else ""          # только сам мод-библиотека: у других модов она бывает вшита внутрь (jar-in-jar)
            if not lib or m.file in gone:
                continue
            needed = any(set(m.ids) & set(o.depends) for o in mods if o.file != m.file and o.file not in gone)
            if not needed:
                add(m.file, "library", f"«{LIBRARIES[lib]}» — вспомогательная библиотека. Ни один мод сборки её не требует, "
                                       "так что она просто занимает память. Если нужен её экран настроек — оставьте.")
                gone.add(m.file)
                changed = True
        if not changed:
            break

    # --- «необязательное»: пересечения и экспериментальные версии. Ломать игру они не обязаны — убирать или нет, решает игрок
    fix = holders(["modernfix"])
    if fix:
        for m in holders(["memoryleakfix"]):
            add(m.file, "maybe", "У ModernFix своя защита от утечек памяти, и она пересекается с Memory Leak Fix. Вместе они не ломают игру — "
                         "но если хотите меньше модов, Memory Leak Fix можно убрать.", fix[0].file)
    for m in holders(["vmp"]):
        add(m.file, "maybe", "VMP («Very Many Players») ускоряет сервер, к которому подключено много игроков. В одиночной игре и в комнате "
                     "на пару друзей прироста почти нет. Если вы поднимаете свой сервер из этой сборки — на сервере он полезен, оставьте.")
    for m in mods:
        main = m.ids[0] if m.ids and m.ids[0] else ""
        if main in OPTIMIZATION_IDS and m.file not in found and any(w in (m.version or "").lower() for w in EXPERIMENTAL_WORDS):
            add(m.file, "maybe", f"«{name(m.file)}» — экспериментальная версия ({m.version}): сам автор считает её сырой. Такие оптимизаторы чаще других "
                         "дают вылеты и «фризы». Если игра работает без сбоев — можно оставить.")

    # то, что убирать нельзя (от мода зависят другие), в список не попадает
    allowed, _blocked = removal_check(mods_dir, loader, game_version, list(found))
    items = [found[f] for f in allowed]
    items.sort(key=lambda x: (x["tier"] != "sure", x["kind"], x["name"].lower()))
    return items


def count_optimization(mods_dir: Path) -> int:
    """Сколько в сборке включённых модов оптимизации (для строки «у вас их N»)."""
    if not mods_dir.is_dir():
        return 0
    return sum(1 for m in _scan(mods_dir) if (m.ids[0] if m.ids and m.ids[0] else "") in OPTIMIZATION_IDS)


def apply(mods_dir: Path, loader: str, game_version: str, files: list[str]) -> dict:
    """Выключает выбранные моды (имя.jar → имя.jar.disabled). Возвращает {disabled: [...], blocked: [{file, reason}]}."""
    allowed, blocked = removal_check(mods_dir, loader, game_version, files)
    done: list[str] = []
    for f in allowed:
        src = mods_dir / f
        dst = src.with_name(src.name + ".disabled")
        if dst.exists():                      # уже есть выключенная копия (возможно, другая версия) — её не трогаем
            blocked[f] = "рядом уже лежит выключенная копия с таким именем — разберите её на вкладке «Моды»"
            continue
        try:
            src.rename(dst)
            done.append(f)
        except OSError as exc:
            blocked[f] = f"файл занят: {exc}"
    return {"disabled": done, "blocked": [{"file": f, "reason": r} for f, r in blocked.items()]}
