"""Можно ли убрать эти моды, не сломав остальные: от них не должны зависеть другие моды сборки.

Общая проверка для «Лишних модов» (core/optaudit.py) и для отмены переноса модов (core/modcopy.py).
Убираем «в уме»: берём все включённые моды сборки без выбранных, гоняем ту же проверку, что перед запуском
(core/modcheck.check_mods), и смотрим, не появилось ли «Не хватает мода X». Если появилось — мод, который этот X даёт,
остаётся в сборке, и в ответе написано, чьей зависимостью он является.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .modcheck import _pick_form, _scan, check_mods


def _enabled(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return sorted(f for f in mods_dir.iterdir() if f.is_file() and f.suffix.lower() == ".jar")


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _dep_issues(mods_dir: Path, loader: str, game_version: str, skip: set[str]) -> dict[str, dict]:
    """Проблемы «не хватает мода» для сборки без файлов skip: id → описание."""
    work = Path(tempfile.mkdtemp(prefix="vmc-safe-"))
    try:
        for f in _enabled(mods_dir):
            if f.name not in skip:
                _link(f, work / f.name)
        return {i["id"]: i for i in check_mods(work, loader, game_version) if i["id"].startswith("dep:")}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def removal_check(mods_dir: Path, loader: str, game_version: str, files: list[str]) -> tuple[list[str], dict[str, str]]:
    """(можно убрать, нельзя: файл → почему). Порядок файлов сохраняется."""
    wanted = [f for f in files if (mods_dir / f).is_file()]
    if not wanted:
        return [], {}
    infos = {m.file: m for m in _scan(mods_dir)}
    all_ids = {i for m in infos.values() for f in (m.forms or [m.__dict__]) for i in f["ids"] if i}
    ids_of = {f: set(_pick_form(infos[f], loader, game_version, all_ids).ids) - {""} for f in wanted if f in infos}
    base = set(_dep_issues(mods_dir, loader, game_version, set()))
    keep: dict[str, str] = {}
    for _ in range(len(wanted) + 1):                       # оставили мод — пересматриваем, не потянул ли он другие
        now = _dep_issues(mods_dir, loader, game_version, {f for f in wanted if f not in keep})
        new = {k: v for k, v in now.items() if k not in base}
        if not new:
            break
        grew = False
        for key, issue in new.items():
            dep = key[4:]
            for f in wanted:
                if f not in keep and dep in ids_of.get(f, set()):
                    keep[f] = issue["text"].split(".")[0]    # «Он нужен для: A, B» — кому нужен мод
                    grew = True
        if not grew:
            break
    allowed = [f for f in wanted if f not in keep]
    return allowed, keep
