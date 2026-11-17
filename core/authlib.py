"""authlib-injector — подключает Ely.by к игре (скины, вход на серверы Ely.by). Скачивается один раз."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .net import download, get_json

LATEST_URL = "https://authlib-injector.yushi.moe/artifact/latest.json"


def ensure_authlib_injector(data_dir: Path) -> Path:
    target = data_dir / "authlib-injector" / "authlib-injector.jar"
    stamp = target.with_suffix(".checked")
    try:   # уже есть и проверяли меньше суток назад — запускаем сразу, без сети
        if target.exists() and time.time() - stamp.stat().st_mtime < 24 * 3600:
            return target
    except OSError:
        pass
    try:
        info = get_json(LATEST_URL, timeout=8, attempts=1)
    except Exception:  # noqa: BLE001 — без сети используем уже скачанный
        if target.exists():
            return target
        raise
    sha256 = info.get("checksums", {}).get("sha256", "")
    if target.exists() and sha256 and hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
        _touch(stamp)
        return target
    new = target.with_suffix(".new")
    new.unlink(missing_ok=True)  # недокачанный файл с прошлого раза иначе не перекачается никогда
    try:
        download(info["download_url"], new)
    except Exception:  # noqa: BLE001 — обновить не вышло: играем со старой версией, если она есть
        if target.exists():
            return target
        raise
    if sha256 and hashlib.sha256(new.read_bytes()).hexdigest() != sha256:
        new.unlink(missing_ok=True)
        raise OSError("authlib-injector: контрольная сумма не совпала")
    new.replace(target)
    _touch(stamp)
    return target


def _touch(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def javaagent_arg(jar: Path) -> str:
    return f"-javaagent:{jar}=ely.by"
