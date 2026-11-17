"""«Какие флаги Java использует лаунчер и почему» — по тем же правилам, что и при запуске (core/launch.py).

Ничего не меняет: только объясняет игроку простыми словами и показывает полный список для тех, кто хочет разобраться.
"""
from __future__ import annotations

import re

from .launch import G1_FLAGS, pick_gc_flags


def explain(java_major: int, ram_gb: int, cores: int, total_ram: int, optimize: bool, compat: bool, own_flags: str) -> dict:
    lines: list[str] = []
    flags: list[str] = [f"-Xmx{ram_gb}G", f"-Xms{min(2, ram_gb)}G"]
    lines.append(f"Память игры: {ram_gb} ГБ" + (f" (у ПК {total_ram} ГБ — системе остаётся запас)" if total_ram else "") +
                 ". Стартовая память — не больше 2 ГБ: остальное игра занимает, когда понадобится.")
    gc = "off"
    if compat:
        lines.append("Режим совместимости включён: флаги лаунчера выключены, игра запускается как в обычных лаунчерах.")
    elif not optimize:
        lines.append("Флаги сборщика мусора выключены в «Настройках» — Java работает со своими настройками по умолчанию.")
    elif re.search(r"-XX:\+Use\w+GC", own_flags or ""):
        gc = "own"
        lines.append("Сборщик мусора выбрали вы сами (в «Дополнительных аргументах JVM») — лаунчер его не меняет.")
    else:
        picked = pick_gc_flags(java_major, ram_gb, cores)
        if picked == G1_FLAGS:
            gc = "G1"
            why = []
            if java_major < 21:
                why.append(f"Java {java_major or '?'} (ZGC — с Java 21)")
            if ram_gb < 6:
                why.append(f"игре выделено {ram_gb} ГБ (для ZGC нужно от 6)")
            if cores < 6:
                why.append(f"{cores or '?'} потоков процессора (для ZGC нужно от 6)")
            lines.append("Сборщик мусора G1 с настройкой Aikar: короткие паузы (до 50 мс) и мало нагрузки на процессор."
                         + (f" ZGC не включён: {'; '.join(why)}." if why else ""))
        else:
            gc = "ZGC"
            lines.append(f"Сборщик мусора ZGC: паузы около миллисекунды, меньше микрофризов. Подходит: Java {java_major}, "
                         f"{ram_gb} ГБ для игры, {cores} потоков процессора.")
        flags += picked
    if not compat and optimize and java_major >= 19:
        flags += ["-XX:+AutoCreateSharedArchive", "-XX:SharedArchiveFile=<папка лаунчера>/cds/<версия>.jsa"]
        lines.append("Кэш классов (AppCDS): после первого запуска игра грузится быстрее. При входе через Ely.by не включается — "
                     "Java не может его записать вместе с authlib-injector.")
    if java_major >= 9 and not compat:
        flags.append("-Xlog:gc:file=logs/vaultmc-gc.log:time,uptime:filecount=2,filesize=2m")
        lines.append("Короткий журнал сборщика мусора (logs/vaultmc-gc.log, до 4 МБ): по нему кнопка «Почему тормозит» находит долгие паузы. "
                     "На производительность почти не влияет.")
    elif java_major and java_major < 9 and not compat:
        lines.append("Журнал сборщика мусора не ведётся: в Java 8 нет нужного флага.")
    if (own_flags or "").strip():
        flags.append(f"{own_flags.strip()}   ← ваши флаги")
    return {"gc": gc, "lines": lines, "flags": flags, "java": java_major}
