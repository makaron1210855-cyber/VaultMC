"""Автообновление лаунчера.

На сайте лежит version.json:
    {"version": "1.0.1", "url": "VaultMC-Setup.exe", "sha256": "...", "notes": "Что нового"}
Лаунчер сравнивает версию со своей, скачивает установщик, проверяет SHA-256
и запускает его с ключом /UPDATE: открывается только окно с полосой установки.
Установщик сам закроет лаунчер, обновит файлы и запустит новую версию. Данные игрока (~/.vaultmc) не трогаются.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import urllib.request

from .net import USER_AGENT, get_json
from .utils import APP_VERSION, LAUNCHER_HOME

# Где лежит version.json. Проверяем по порядку: первый ответивший сайт и есть источник обновлений.
# Только сайты, которые принадлежат нам: лаунчер скачивает и запускает установщик с того сайта, который ответил.
# Старый адрес vaultmc.netlify.app убран — сайта там больше нет, и это имя мог бы занять кто угодно.
UPDATE_MANIFESTS = [
    "https://vaultmc.vaultmc.workers.dev/version.json",
]
UPDATE_MANIFEST = next((u for u in UPDATE_MANIFESTS if u), "")


class UpdateError(Exception):
    pass


def _ver(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:4]) or (0,)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return _ver(remote) > _ver(local)


def _notes_for(data: dict, new: str) -> str:
    """
    В version.json есть два вида описания:
      notes   — готовый текст для старых лаунчеров (до 3.0.2), он у всех одинаковый;
      changes — {версия: [строки]}: из него собираем только то, что новое именно для этой версии,
                а строку «масштабное обновление» добавляет само окно, когда меняется первая цифра.
    """
    changes = data.get("changes")
    if not isinstance(changes, dict):
        return str(data.get("notes", ""))
    from .changelog import format_notes
    return format_notes(changes, _ver(APP_VERSION), _ver(new)) or str(data.get("notes", ""))


def check() -> dict:
    """{'available': bool, 'version', 'notes', 'url', 'sha256', 'current'}; UpdateError — нет связи."""
    urls = [u for u in UPDATE_MANIFESTS if u]
    if not urls:
        raise UpdateError("Сервер обновлений ещё не настроен")
    data, source, last_error = None, "", None
    for url in urls:
        try:
            data, source = get_json(url, timeout=10, attempts=1), url
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if data is None:
        raise UpdateError(f"Не удалось проверить обновления: {last_error}")
    version = str(data.get("version", ""))
    return {
        "available": bool(version) and is_newer(version),
        "version": version,
        "notes": _notes_for(data, version),
        "url": urllib.parse.urljoin(source, str(data.get("url", "VaultMC-Setup.exe"))),
        "sha256": str(data.get("sha256", "")).lower(),
        "current": APP_VERSION,
    }


BLOCKED_HINT = ("Windows Defender (или антивирус) заблокировал скачанный установщик. Это ложное срабатывание: "
                "у VaultMC пока нет платной цифровой подписи. Скачайте установщик с сайта вручную.")


def _file_error(exc: OSError, action: str) -> UpdateError:
    """Понятный текст вместо «[Errno 22] Invalid argument». WinError 225/226 — файл заблокирован антивирусом."""
    if getattr(exc, "winerror", None) in (225, 226) or exc.errno == 22:
        return UpdateError(BLOCKED_HINT)
    if isinstance(exc, PermissionError):
        return UpdateError(f"{action}: файл занят другой программой (часто антивирус проверяет его). "
                           "Подождите минуту и нажмите «Обновить» ещё раз.")
    return UpdateError(f"{action}: {exc}")


def download_installer(info: dict, progress=None) -> Path:
    """Скачивает установщик и сверяет SHA-256 (считается прямо при скачивании, файл заново не читаем)."""
    folder = LAUNCHER_HOME / "updates"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("VaultMC-Setup-*"):   # прошлые попытки
            try:
                old.unlink()
            except OSError:
                pass
    except OSError as exc:
        raise _file_error(exc, "Не удалось подготовить папку для обновления") from exc
    dest = folder / f"VaultMC-Setup-{info['version']}.exe"
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(info["url"], headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(256 * 1024):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать обновление: сайт ответил {exc.code}"
                          + (" — файла установщика нет на сайте" if exc.code == 404 else "")) from exc
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, urllib.error.URLError) or not getattr(exc, "filename", None):
            raise UpdateError(f"Не удалось скачать обновление: {exc}") from exc
        raise _file_error(exc, "Не удалось сохранить обновление") from exc
    if info.get("sha256") and digest.hexdigest() != info["sha256"]:
        tmp.unlink(missing_ok=True)
        raise UpdateError("Файл обновления повреждён (не совпала контрольная сумма). Попробуйте ещё раз.")
    try:
        tmp.replace(dest)
    except OSError as exc:
        raise _file_error(exc, "Не удалось сохранить обновление") from exc
    return dest


def run_installer(path: Path) -> None:
    """Запускает установщик отдельно от лаунчера: он закроет нас, обновит файлы и запустит снова."""
    if sys.platform != "win32":
        raise UpdateError("Автообновление работает только в Windows")
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([str(path), "/UPDATE"], creationflags=flags, close_fds=True,
                         cwd=os.path.dirname(path))
    except OSError as exc:
        raise _file_error(exc, "Не удалось запустить установщик") from exc
