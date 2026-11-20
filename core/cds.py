"""Базовый архив классов Java (CDS) для Java, которую ставит лаунчер.

В Java от Mojang (shared/runtime/java-runtime-*) нет bin/server/classes.jsa, поэтому CDS не работал вовсе.
`java -Xshare:dump` создаёт его за ~0,5 с; для ZGC (без сжатых указателей) нужен отдельный classes_nocoops.jsa.
Чужую Java (Program Files и т. п.) не трогаем — только папку runtime лаунчера.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .utils import no_window_flags

ARCHIVES = (("classes.jsa", []), ("classes_nocoops.jsa", ["-XX:-UseCompressedOops"]))


def _console_java(java: Path) -> Path:
    alt = java.with_name("java.exe")
    return alt if java.name.lower() == "javaw.exe" and alt.is_file() else java


def ensure_base_cds(java: Path, runtime_root: Path, log: Callable[[str], None] = lambda _m: None) -> list[str]:
    """Создаёт недостающие базовые архивы. Возвращает имена созданных. Ошибки не мешают запуску игры."""
    try:
        java = java.resolve()
        java.relative_to(runtime_root.resolve())
    except (OSError, ValueError):
        return []
    server = java.parent / "server"
    if not (server / "jvm.dll").is_file() and not (server / "libjvm.so").is_file():
        return []
    made = []
    for name, extra in ARCHIVES:
        if (server / name).is_file():
            continue
        try:
            res = subprocess.run([str(_console_java(java)), *extra, "-Xshare:dump"], capture_output=True,
                                 timeout=60, creationflags=no_window_flags())
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"[Java] архив классов {name} не создан: {exc}")
            continue
        if res.returncode == 0 and (server / name).is_file():
            made.append(name)
        else:
            log(f"[Java] архив классов {name} не создан (код {res.returncode})")
    if made:
        log(f"[Java] создан архив классов: {', '.join(made)} — Java будет запускаться быстрее")
    return made
