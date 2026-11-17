"""
Автоматическая установка Java — как в официальном лаунчере.

Mojang публикует готовые сборки Java для игры («java-runtime-gamma» = Java 17,
«java-runtime-delta» = Java 21, «jre-legacy» = Java 8 и т.д.). Какая нужна — написано
в version.json игры (javaVersion.component). Лаунчер скачивает её в data_dir/runtime/
один раз и дальше использует сам. Пользователю ничего ставить не нужно.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

from .installer import DownloadTask, Installer
from .net import download, get_json
from .utils import is_windows

RUNTIME_INDEX = ("https://piston-meta.mojang.com/v1/products/java-runtime/"
                 "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
# если в version.json нет названия компонента — подбираем по номеру Java
COMPONENT_BY_MAJOR = {8: "jre-legacy", 16: "java-runtime-alpha", 17: "java-runtime-gamma",
                      21: "java-runtime-delta", 25: "java-runtime-epsilon"}


def platform_key() -> str:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if is_windows():
        return "windows-arm64" if arm else ("windows-x86" if machine in ("x86", "i386", "i686") else "windows-x64")
    if sys.platform == "darwin":
        return "mac-os-arm64" if arm else "mac-os"
    return "linux-i386" if machine in ("i386", "i686") else "linux"


def java_executable(root: Path) -> Path:
    if is_windows():
        return root / "bin" / "javaw.exe"
    if sys.platform == "darwin":
        return root / "jre.bundle" / "Contents" / "Home" / "bin" / "java"
    return root / "bin" / "java"


def runtime_dir(data_dir: Path, component: str) -> Path:
    return data_dir / "runtime" / component / platform_key()


def installed_runtime(data_dir: Path, component: str) -> Path | None:
    exe = java_executable(runtime_dir(data_dir, component))
    return exe if exe.exists() else None


ADOPTIUM = "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/jre/hotspot/normal/eclipse"


def ensure_runtime(installer: Installer, component: str | None, major: int) -> Path:
    """
    Скачивает (или находит уже скачанную) Java для игры. Возвращает путь к java/javaw.
    Источник 1 — официальная сборка Mojang, источник 2 (запасной) — Eclipse Temurin (adoptium.net).
    """
    try:
        return ensure_mojang_runtime(installer, component, major)
    except Exception as exc:  # noqa: BLE001
        installer.log(f"[Java] Mojang не отдал Java {major} ({exc}) — пробую Eclipse Temurin")
    return ensure_temurin(installer, major)


def ensure_temurin(installer: Installer, major: int) -> Path:
    root = installer.game_dir / "runtime" / f"temurin-{major}"
    exe_name = "javaw.exe" if is_windows() else "java"
    found = next(iter(sorted(root.glob(f"**/bin/{exe_name}"))), None) if root.is_dir() else None
    if found:
        return found
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else ("x86" if machine in ("x86", "i386", "i686") else "x64")
    os_name = "windows" if is_windows() else ("mac" if sys.platform == "darwin" else "linux")
    url = ADOPTIUM.format(major=major, os=os_name, arch=arch)
    archive = root.parent / f"temurin-{major}.{'zip' if is_windows() else 'tar.gz'}"
    installer.progress(0.3, f"Скачиваю Java {major} (Temurin)...")
    installer.log(f"[Java] Скачиваю Eclipse Temurin {major}: {url}")
    archive.unlink(missing_ok=True)
    download(url, archive, cancel=installer.cancel)
    installer.progress(0.8, f"Распаковываю Java {major}...")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    if is_windows():
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(root)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(root)
    archive.unlink(missing_ok=True)
    found = next(iter(sorted(root.glob(f"**/bin/{exe_name}"))), None)
    if not found:
        raise RuntimeError(f"не удалось распаковать Java {major}")
    installer.log(f"[Java] Готово: {found}")
    return found


def ensure_mojang_runtime(installer: Installer, component: str | None, major: int) -> Path:
    component = component or COMPONENT_BY_MAJOR.get(major) or "java-runtime-delta"
    data_dir = installer.game_dir
    ready = installed_runtime(data_dir, component)
    if ready:
        return ready

    installer.progress(0.02, f"Скачиваю Java {major}...")
    installer.log(f"[Java] Нужна Java {major} ({component}) — скачиваю официальную сборку Mojang")
    index = get_json(RUNTIME_INDEX)
    entries = index.get(platform_key(), {}).get(component) or []
    if not entries:
        raise RuntimeError(f"Для этой системы нет Java {major} от Mojang. Установите Java {major} вручную (adoptium.net)")
    manifest = get_json(entries[0]["manifest"]["url"])
    root = runtime_dir(data_dir, component)

    tasks: list[DownloadTask] = []
    executables: list[Path] = []
    for rel, info in manifest.get("files", {}).items():
        target = root / rel
        if info.get("type") == "directory":
            target.mkdir(parents=True, exist_ok=True)
        elif info.get("type") == "file":
            raw = info["downloads"]["raw"]
            tasks.append(DownloadTask(raw["url"], target, raw.get("sha1"), raw.get("size")))
            if info.get("executable"):
                executables.append(target)
        # type == "link" встречается только на macOS/Linux — Java работает и без них
    installer.run_tasks(tasks, f"Java {major}")
    if not is_windows():
        for exe in executables:
            if exe.exists():
                os.chmod(exe, 0o755)
    exe = java_executable(root)
    if not exe.exists():
        raise RuntimeError("Java скачалась не полностью — попробуйте ещё раз")
    installer.log(f"[Java] Готово: {exe}")
    return exe
