"""Проверка файлов лаунчера: не испортил ли их антивирус, сбой диска или прерванное обновление.

При сборке (tools/release.py) в папку app кладётся files.json — контрольная сумма каждого файла.
При запуске лаунчер в фоне сверяет файлы; если что-то пропало или изменилось — предлагает «Починить»
(переустановка той же или новой версии через подписанное автообновление). Запуск из исходников
(без files.json) не проверяется. Папка python не проверяется: она большая, и её портят редко.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST = "files.json"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".log")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(app_dir: Path):
    for p in sorted(app_dir.rglob("*")):
        rel = p.relative_to(app_dir)
        if not p.is_file() or rel.as_posix() == MANIFEST or SKIP_DIRS & set(rel.parts) or p.name.endswith(SKIP_SUFFIXES):
            continue
        yield rel.as_posix(), p


def build(app_dir: Path, version: str) -> int:
    """Записывает files.json (при сборке выпуска). Возвращает число файлов."""
    files = {rel: _sha256(p) for rel, p in _files(app_dir)}
    (app_dir / MANIFEST).write_text(json.dumps({"version": version, "files": files}, indent=0), encoding="utf-8")
    return len(files)


def check(app_dir: Path) -> list[str]:
    """Испорченные или пропавшие файлы (пусто — всё в порядке или проверять нечем)."""
    try:
        data = json.loads((app_dir / MANIFEST).read_text(encoding="utf-8"))
        files = data["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    bad = []
    root = app_dir.resolve()
    for rel, sha in files.items():
        p = (app_dir / rel).resolve()
        if root not in p.parents:
            continue
        try:
            if not p.is_file():
                bad.append(f"{rel} — нет файла")
            elif _sha256(p) != sha:
                bad.append(f"{rel} — изменён")
        except OSError:
            bad.append(f"{rel} — не читается")
    return bad
