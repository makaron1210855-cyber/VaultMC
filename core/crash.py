"""
Разбор вылетов игры: ищет в выводе игры и crash-report известные причины,
объясняет их простыми словами и предлагает исправление в один клик.

Находка: {"title", "text", "fix": {"action", "arg", "label"} | None}
Действия (выполняет backend.api.crash_fix):
  java_auto     — включить автоподбор Java
  ram           — дать сборке больше памяти (arg = ГБ)
  ram_down      — уменьшить память (arg = ГБ)
  find_mod      — открыть каталог с поиском мода (arg = id мода)
  disable_mod   — выключить мод (arg = имя файла)
  logs          — открыть консоль
"""

from __future__ import annotations

import re
from pathlib import Path

# версия class-файла → версия Java
CLASS_VERSIONS = {52: 8, 53: 9, 54: 10, 55: 11, 56: 12, 57: 13, 58: 14, 59: 15, 60: 16, 61: 17,
                  62: 18, 63: 19, 64: 20, 65: 21, 66: 22, 67: 23, 68: 24, 69: 25}

GPU_DLLS = {"atio6axx": "AMD", "atioglxx": "AMD", "nvoglv64": "NVIDIA", "nvoglv32": "NVIDIA",
            "ig9icd64": "Intel", "ig7icd64": "Intel", "ig75icd64": "Intel", "ig8icd64": "Intel", "igxelpicd64": "Intel"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_mod_file(mods_dir: Path, mod_id: str) -> str:
    """Файл мода по его id (sodium → sodium-fabric-0.5.11.jar). Пусто, если не нашли."""
    if not mods_dir.is_dir() or not mod_id:
        return ""
    key = _norm(mod_id)
    best = ""
    for f in mods_dir.iterdir():
        if f.suffix != ".jar":
            continue
        name = _norm(f.stem)
        if name.startswith(key) and (not best or len(f.name) < len(best)):
            best = f.name
    return best


def analyze(log_text: str, report_text: str = "", mods_dir: Path | None = None, ram_gb: int = 4,
            exit_code: int = 1) -> list[dict]:
    text = log_text + "\n" + report_text
    found: list[dict] = []
    seen: set[str] = set()

    def add(key: str, title: str, body: str, fix: dict | None = None) -> None:
        if key not in seen:
            seen.add(key)
            found.append({"title": title, "text": body, "fix": fix})

    # --- Java не той версии
    m = re.search(r"class file version (\d+)\.\d+\).*?up to (\d+)\.\d+", text)
    if m or "UnsupportedClassVersionError" in text:
        need = CLASS_VERSIONS.get(int(m.group(1)), 0) if m else 0
        have = CLASS_VERSIONS.get(int(m.group(2)), 0) if m else 0
        add("java", "Не та версия Java",
            f"Игре или моду нужна Java {need or 'новее'}" + (f", а запущена Java {have}" if have else "") +
            ". Лаунчер может сам скачать нужную.",
            {"action": "java_auto", "arg": "", "label": "Подбирать Java автоматически"})

    # --- Ely.by недоступен: authlib-injector не смог скачать описание сервера, и игра закрылась сразу после старта
    if re.search(r"authlib-injector\] \[ERROR\] Failed to fetch metadata", text):
        add("elyby-net", "Нет связи с Ely.by",
            "Игра с аккаунтом Ely.by не смогла связаться с серверами Ely.by (account.ely.by) и закрылась. Обычно это "
            "блокировка сайта провайдером, VPN/антивирус или временный сбой Ely.by (в прошлый раз всё работало). "
            "Попробуйте ещё раз через минуту, включите/выключите VPN. Если Ely.by недоступен совсем — войдите оффлайн-аккаунтом: "
            "играть можно так же, только без скина с Ely.by.", None)

    # --- память
    if re.search(r"OutOfMemoryError|GC overhead limit exceeded", text):
        add("oom", "Игре не хватило памяти",
            f"Сейчас сборке выделено {ram_gb} ГБ. С большим количеством модов или шейдеров нужно больше.",
            {"action": "ram", "arg": ram_gb + 2, "label": f"Выделить {ram_gb + 2} ГБ"})
    if re.search(r"Could not reserve enough space for (?:object heap|\d+KB object heap)", text):
        add("heap", "Не удалось выделить память",
            "Java не смогла занять столько памяти: либо её мало свободно, либо стоит 32-битная Java.",
            {"action": "ram_down", "arg": max(2, ram_gb - 2), "label": f"Уменьшить до {max(2, ram_gb - 2)} ГБ"})

    # --- нет нужного мода (Fabric): «Mod 'X' (x) 1.0 requires any version of 'Fabric API' (fabric-api), which is missing!»
    for mod, name, pid in re.findall(r"Mod '([^']+)' \([^)]*\) \S+ requires [^\n]*? of (?:mod )?'?([^'\n,(]+?)'?"
                                     r"(?: \(([\w\-.]+)\))?, which is missing", text):
        dep = pid or name.strip()
        add(f"dep:{dep}", f"Не хватает мода «{name.strip()}»",
            f"Мод «{mod}» не работает без «{name.strip()}». Установите его из каталога.",
            {"action": "find_mod", "arg": dep, "label": f"Найти {name.strip()}"})
    # --- нет нужного мода (Forge / NeoForge)
    for dep, by in re.findall(r"Mod ID: '([\w\-]+)', Requested by: '([\w\-]+)'[^\n]*Actual version: '\[MISSING\]'", text):
        add(f"dep:{dep}", f"Не хватает мода «{dep}»",
            f"Мод «{by}» не работает без «{dep}». Установите его из каталога.",
            {"action": "find_mod", "arg": dep, "label": f"Найти {dep}"})

    # --- мод для другой версии / несовместимость (Fabric)
    for mod, need, have in re.findall(r"Mod '([^']+)' \([^)]*\) \S+ requires (?:version )?([^\n]*?) of '?Minecraft'? \(minecraft\), "
                                      r"but only the wrong version is present: ([\w.\-]+)", text):
        add(f"mc:{mod}", f"«{mod}» — для другой версии игры",
            f"Мод требует Minecraft {need}, а в сборке {have}. Удалите его или найдите версию под вашу игру.", None)
    for mod in re.findall(r"Mod '([^']+)' \([^)]*\) \S+ is incompatible with", text):
        add(f"inc:{mod}", f"Мод «{mod}» конфликтует с другим",
            "В сборке есть два мода, которые не работают вместе. Подробности — в консоли.", None)

    # --- дубликаты
    if re.search(r"[Dd]uplicate mods? (?:found|for)|DuplicateModsFoundException|Found duplicate mods", text):
        add("dup", "Один мод установлен дважды",
            "В папке mods лежат две версии одного мода. Оставьте одну (обычно — более новую).", None)

    # --- мод для другого загрузчика
    if re.search(r"(?:is a|requires) (?:Forge|NeoForge) mod|fabric\.mod\.json.*not found|Found Forge mod", text):
        add("loader", "Мод от другого загрузчика",
            "Похоже, в сборку попал мод не для того загрузчика (Forge-мод в Fabric или наоборот).", None)

    # --- OptiFine вместе с модами-заменителями рендера (они несовместимы)
    if re.search(r"OptiFine(?:TransformationService| detected)|OptiFine_[\w.]+\.jar", text, re.I):
        rivals = [name for name, pat in (("Embeddium", r"embeddium"), ("Sodium", r"\bsodium\b"), ("Rubidium", r"rubidium"),
                                         ("ModernFix", r"modernfix"), ("Iris", r"\biris\b"), ("Oculus", r"oculus"))
                  if re.search(pat, text, re.I)]
        if rivals:
            optifine = ""
            if mods_dir and mods_dir.is_dir():
                optifine = next((f.name for f in mods_dir.iterdir()
                                 if f.suffix == ".jar" and "optifine" in f.name.lower()), "")
            add("optifine", "OptiFine несовместим с " + ", ".join(rivals),
                "OptiFine переделывает отрисовку игры, и моды " + ", ".join(rivals) + " тоже. Вместе они ломают запуск. "
                "Оставьте что-то одно: для FPS и шейдеров вместо OptiFine лучше Embeddium + Oculus (Forge) "
                "или Sodium + Iris (Fabric).",
                {"action": "disable_mod", "arg": optifine, "label": "Выключить OptiFine"} if optifine else None)

    # --- OptiFine не подходит к этой версии Forge: он подменяет классы игры своими копиями, и Forge не находит
    #     в них нужный метод (например, ResourceLocation.parse в Forge 47.4 для 1.20.1)
    if "optifine" not in seen and re.search(r"OptiFine", text) and \
            re.search(r"java\.lang\.(?:NoSuchMethodError|NoSuchFieldError|AbstractMethodError|IncompatibleClassChangeError)", text):
        optifine = ""
        if mods_dir and mods_dir.is_dir():
            optifine = next((f.name for f in mods_dir.iterdir()
                             if f.suffix == ".jar" and "optifine" in f.name.lower()), "")
        forge = re.search(r"Forge version: ([\d.]+)|MinecraftForge v([\d.]+)", text)
        forge_v = (forge.group(1) or forge.group(2)) if forge else ""
        add("optifine-forge", "OptiFine не подходит к этой версии Forge",
            f"OptiFine заменяет часть файлов игры своими, а Forge{' ' + forge_v if forge_v else ''} ждёт в них то, чего "
            "у этой версии OptiFine нет, — игра падает при загрузке. Выключите OptiFine (для FPS и шейдеров лучше "
            "Embeddium + Oculus) или создайте сборку на более старой версии Forge, с которой работает ваш OptiFine.",
            {"action": "disable_mod", "arg": optifine, "label": "Выключить OptiFine"} if optifine else None)

    # --- сломанный мод (Mixin)
    for mod in re.findall(r"Mixin apply (?:for mod|failed for) ([\w\-]+)", text):
        file = find_mod_file(mods_dir, mod) if mods_dir else ""
        add(f"mixin:{mod}", f"Ошибка в моде «{mod}»",
            "Этот мод не смог встроиться в игру — обычно он для другой версии или конфликтует с другим модом.",
            {"action": "disable_mod", "arg": file, "label": f"Выключить {file}"} if file else None)

    # --- мод не встроился (Mixin): «… in embeddium.mixins.json:…» — называем мод по имени его mixin-файла
    err_re = re.compile(r"Mixin(?:TransformerError|ApplyError)|InjectionError|InvalidMixinException|Critical injection failure")
    if err_re.search(text):
        # смотрим только строку ошибки и 4 строки после неё: раньше брали первые упоминания со всего лога
        # и могли обвинить мод, который просто писал предупреждения при загрузке
        lines = text.splitlines()
        near = "\n".join("\n".join(lines[i:i + 5]) for i, ln in enumerate(lines) if err_re.search(ln))
        for mod in list(dict.fromkeys(re.findall(r"\bin ([a-z][\w\-]*?)(?:[.\-_][\w\-]+)*\.mixins\.json", near)))[:2]:
            if f"mixin:{mod}" in seen or mod in ("minecraft", "forge", "neoforge", "fabric"):
                continue
            file = find_mod_file(mods_dir, mod) if mods_dir else ""
            optifine_note = " Похоже, он конфликтует с OptiFine — см. пункт выше." if "optifine" in seen else ""
            add(f"mixin:{mod}", f"Мод «{mod}» несовместим с другим модом или версией игры",
                "Мод не смог встроиться в игру при запуске. Обычно так бывает, когда два мода меняют одно и то же "
                "(например, OptiFine вместе с Embeddium/Sodium) или мод сделан для другой версии." + optifine_note,
                {"action": "disable_mod", "arg": file, "label": f"Выключить {file}"} if file and not optifine_note else None)

    # --- подозреваемые моды из crash-report
    for line in re.findall(r"Suspected Mods?: ([^\n]+)", report_text):
        if line.strip().upper() in ("NONE", "UNKNOWN"):
            continue
        ids = re.findall(r"\(([\w\-]+)\)", line) or [line.strip()]
        for mod in ids[:3]:
            if mod.lower() in ("minecraft", "forge", "neoforge", "fabricloader", "java"):
                continue
            file = find_mod_file(mods_dir, mod) if mods_dir else ""
            add(f"susp:{mod}", f"Вероятный виновник: «{mod}»",
                "Игра указала на этот мод в отчёте о вылете. Попробуйте выключить его и запустить снова.",
                {"action": "disable_mod", "arg": file, "label": f"Выключить {file}"} if file else None)

    # --- драйвер видеокарты: только если игра упала ВНУТРИ драйвера («Problematic frame: C [ig9icd64.dll+0x…]»).
    #     Раньше хватало, что файл драйвера просто упомянут — а он есть в списке загруженных файлов любого отчёта о вылете,
    #     и лаунчер советовал обновить драйвер при любой ошибке мода.
    crash_frame = re.findall(r"#\s+C\s+\[([\w.]+?)\.dll\+0x", text, re.I)
    dll = next((GPU_DLLS[f.lower()] for f in crash_frame if f.lower() in GPU_DLLS), "")
    if dll or re.search(r"Pixel format not accelerated|WGL: The driver does not appear to support OpenGL|"
                        r"GLFW error 65542|No OpenGL context", text):
        add("gpu", "Проблема с драйвером видеокарты",
            f"Вылет произошёл в драйвере {dll or 'видеокарты'}. Обновите драйвер с сайта "
            f"{ {'AMD': 'amd.com', 'NVIDIA': 'nvidia.com', 'Intel': 'intel.com'}.get(dll, 'производителя видеокарты') } "
            "и перезагрузите компьютер.", None)

    # --- общее описание, если ничего не узнали
    if not found:
        desc = re.search(r"^Description: (.+)$", report_text, re.M)
        exc = re.search(r"^((?:[\w$]+\.)+[\w$]*(?:Exception|Error)[^\n]{0,160})", text, re.M)
        detail = (desc.group(1) if desc else "") or (exc.group(1) if exc else "")
        add("unknown", "Причину не удалось определить",
            (f"Ошибка: {detail}. " if detail else "") +
            "Посмотрите последние строки в консоли. Если недавно ставили моды — выключите их по одному.",
            {"action": "logs", "arg": "", "label": "Открыть консоль"})
    return found
