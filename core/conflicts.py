"""
Известные конфликты модов — проверка перед запуском.

Если в сборке включены два мода, которые вместе ломают игру, лаунчер выключает «лишний»
(переименовывает в .jar.disabled — файл остаётся, его можно включить обратно в списке модов)
и объясняет, почему. В список попадают только конфликты, о которых пишут сами авторы модов
или которые однозначно ломают запуск.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Conflict:
    keep: tuple[str, ...]      # если есть любой из этих модов...
    drop: tuple[str, ...]      # ...эти выключаем
    reason: str


# имена — по началу имени файла без регистра и знаков (krypton-0.2.3.jar → krypton)
CONFLICTS = (
    Conflict(("e4mc",), ("krypton",),
             "Krypton (ускоритель сети) конфликтует с модом комнат e4mc — игра вылетает при входе друга (известная ошибка, Krypton issue #152)"),
    Conflict(("e4mc",), ("e4all",),
             "e4all — это переделанный e4mc, вдвоём они не работают; оставлен e4mc"),
    Conflict(("sodium", "embeddium", "rubidium", "iris", "oculus"), ("optifine",),
             "OptiFine несовместим с Sodium/Embeddium/Iris/Oculus — они переделывают одну и ту же отрисовку"),
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _enabled_jars(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return [f for f in mods_dir.iterdir() if f.is_file() and f.suffix.lower() == ".jar"]


def _match(files: list[Path], names: tuple[str, ...]) -> list[Path]:
    return [f for f in files if any(_norm(f.stem).startswith(_norm(n)) for n in names)]


def fix_conflicts(mods_dir: Path, log=print) -> list[str]:
    """Выключает лишние моды из известных конфликтов. Возвращает понятные строки «что выключено и почему»."""
    done: list[str] = []
    for rule in CONFLICTS:
        files = _enabled_jars(mods_dir)
        if not _match(files, rule.keep):
            continue
        for f in _match(files, rule.drop):
            target = f.with_name(f.name + ".disabled")
            try:
                if target.exists():
                    target.unlink()
                f.rename(target)
            except OSError as exc:
                log(f"[Конфликт] Не удалось выключить {f.name}: {exc}")
                continue
            msg = f"Выключен {f.name}: {rule.reason}"
            log(f"[Конфликт] {msg}. Включить обратно можно в модах сборки")
            done.append(msg)
    return done
