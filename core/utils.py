"""Общие утилиты: пути, ОС, поиск Java, оффлайн-UUID, форматирование."""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "VaultMC"
APP_VERSION = "7.0.6"


def _build_id() -> str:
    """Отпечаток кода: разные сборки одной версии не путаются друг с другом."""
    import hashlib
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha1()
    for sub in ("core", "backend", "web/js"):
        for f in sorted((root / sub).rglob("*")):
            if f.suffix in (".py", ".js"):
                try:
                    h.update(f.read_bytes())
                except OSError:
                    pass
    return h.hexdigest()[:10]


APP_BUILD = _build_id()
LAUNCHER_HOME = Path.home() / ".vaultmc"         # конфиг, аккаунты, сборки, общие файлы игры


def timing_log(line: str) -> None:
    """Замеры запуска — в ~/.vaultmc/launcher.log, чтобы их можно было прочитать после запуска (не только в консоли)."""
    import time
    try:
        with open(LAUNCHER_HOME / "launcher.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [замер] {line}\n")
    except OSError:
        pass
OLD_LAUNCHER_HOME = Path.home() / ".neon_launcher"  # папка прежнего названия (Neon Launcher)
if not LAUNCHER_HOME.exists() and OLD_LAUNCHER_HOME.is_dir():  # переезд данных: ничего не теряется
    try:
        OLD_LAUNCHER_HOME.rename(LAUNCHER_HOME)
    except OSError:
        try:
            shutil.copytree(OLD_LAUNCHER_HOME, LAUNCHER_HOME)
        except OSError:
            LAUNCHER_HOME = OLD_LAUNCHER_HOME
INSTANCES_DIR = LAUNCHER_HOME / "instances"
DEFAULT_DATA_DIR = LAUNCHER_HOME / "shared"       # versions/, libraries/, assets/ — общие для всех сборок


def resource_path(relative: str) -> Path:
    """Путь к файлам из assets/ — работает и из исходников, и из собранного PyInstaller .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative
NICKNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


# ----------------------------------------------------------------------------- ОС и пути

def is_windows() -> bool:
    return platform.system() == "Windows"


def mojang_os_name() -> str:
    """Название ОС в формате version.json: windows / osx / linux."""
    return {"Windows": "windows", "Darwin": "osx"}.get(platform.system(), "linux")


def default_minecraft_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / ".minecraft"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


def open_in_file_manager(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if is_windows():
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def no_window_flags() -> int:
    """Флаг, чтобы на Windows не мелькало окно консоли при запуске дочерних процессов."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0


def maven_to_path(name: str) -> str:
    """'group:artifact:version[:classifier][@ext]' -> относительный путь в libraries/."""
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) > 3 else ""
    return f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{classifier}.{ext}"


def format_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd) if is_windows() else shlex.join(cmd)


def format_duration(seconds: int) -> str:
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def offline_uuid(nickname: str) -> str:
    """Тот же алгоритм, что UUID.nameUUIDFromBytes("OfflinePlayer:<ник>") в Java."""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{nickname}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest)).hex


def available_ram_mb() -> int | None:
    """Свободная оперативная память в МБ (Windows — GlobalMemoryStatusEx, Linux — MemAvailable). Не вышло — None."""
    try:
        if is_windows():
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            return int(status.ullAvailPhys // 1024 ** 2)
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001 — проверка необязательная
        return None
    return None


def low_memory_warning(ram_gb: int, available_mb: int | None) -> str:
    """Текст-предупреждение, если игре отдано больше, чем сейчас свободно (порог 90 %, как у Prism); иначе пустая строка."""
    if not available_mb or ram_gb <= 0:
        return ""
    if ram_gb * 1024 * 0.9 <= available_mb:
        return ""
    return (f"Игре отдано {ram_gb} ГБ, а свободно сейчас около {max(0.1, round(available_mb / 1024, 1))} ГБ. "
            f"Возможны подвисания и вылеты — закройте лишние программы или уменьшите память сборки в её настройках")


def java_is_32bit(path: str) -> bool:
    """Java 32-битная? Windows — по заголовку PE (машина x86), иначе по файлу release (OS_ARCH). Не понятно — False."""
    try:
        exe = Path(path)
        if exe.suffix.lower() == ".exe" and exe.exists():
            with open(exe, "rb") as f:
                head = f.read(4096)
            if head[:2] == b"MZ" and len(head) >= 0x40:
                off = int.from_bytes(head[0x3C:0x40], "little")
                if off + 6 <= len(head) and head[off:off + 4] == b"PE\0\0":
                    return int.from_bytes(head[off + 4:off + 6], "little") == 0x014C
        release = (exe.parent.parent / "release").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'OS_ARCH="([^"]+)"', release)
        return bool(m) and m.group(1).lower() in ("x86", "i386", "i586", "i686")
    except OSError:
        return False


def total_ram_gb() -> int | None:
    """Объём физической памяти в ГБ (без сторонних библиотек)."""
    try:
        if is_windows():
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return round(status.ullTotalPhys / 1024 ** 3)
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return round(int(out) / 1024 ** 3)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2)
    except Exception:  # noqa: BLE001 — информация необязательная
        return None
    return None


# ----------------------------------------------------------------------------- Java

@dataclass(frozen=True)
class JavaInstall:
    path: str
    major: int


_JAVA_VERSION_CACHE: dict[str, int | None] = {}


def java_major_version(path: str) -> int | None:
    """Мажорная версия Java (8, 17, 21...) по выводу `java -version`."""
    if path in _JAVA_VERSION_CACHE:
        return _JAVA_VERSION_CACHE[path]
    exe = Path(path)
    # Быстрый путь: у JRE/JDK рядом лежит файл release с JAVA_VERSION — не нужно запускать java (это секунды)
    try:
        release = (exe.parent.parent / "release").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'JAVA_VERSION="(\d+)(?:\.(\d+))?', release)
        if m:
            major = int(m.group(2) or 0) if m.group(1) == "1" else int(m.group(1))
            _JAVA_VERSION_CACHE[path] = major
            return major
    except OSError:
        pass
    probe = exe.with_name("java.exe") if exe.name.lower() == "javaw.exe" and exe.with_name("java.exe").exists() else exe
    major = None
    try:
        result = subprocess.run([str(probe), "-version"], capture_output=True, text=True, timeout=10,
                                creationflags=no_window_flags())
        match = re.search(r'version "(\d+)(?:\.(\d+))?', result.stderr + result.stdout)
        if match:
            major = int(match.group(2) or 0) if match.group(1) == "1" else int(match.group(1))
    except (OSError, subprocess.SubprocessError):
        major = None
    _JAVA_VERSION_CACHE[path] = major
    return major


def find_java_installs(game_dir: Path | None = None) -> list[JavaInstall]:
    """Ищет Java: JAVA_HOME, PATH, стандартные папки JDK и рантаймы официального лаунчера."""
    exe_names = ["javaw.exe", "java.exe"] if is_windows() else ["java"]
    candidates: list[Path] = []

    def add_bin(bin_dir: Path) -> None:
        for name in exe_names:
            if (bin_dir / name).exists():
                candidates.append(bin_dir / name)
                return

    if os.environ.get("JAVA_HOME"):
        add_bin(Path(os.environ["JAVA_HOME"]) / "bin")
    for name in exe_names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
            break

    roots: list[Path] = []
    if is_windows():
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                roots += [Path(base) / v for v in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu",
                                                   "BellSoft", "Amazon Corretto")]
    elif sys.platform == "darwin":
        roots.append(Path("/Library/Java/JavaVirtualMachines"))
    else:
        roots.append(Path("/usr/lib/jvm"))
    for root in roots:
        if root.is_dir():
            for d in root.iterdir():
                add_bin(d / "bin")
                add_bin(d / "Contents" / "Home" / "bin")

    if game_dir and (game_dir / "runtime").is_dir():  # Java от официального лаунчера
        candidates += list((game_dir / "runtime").glob(f"**/bin/{exe_names[0]}"))

    result: list[JavaInstall] = []
    seen: set[Path] = set()
    for c in candidates:
        try:
            key = c.resolve().parent
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        major = java_major_version(str(c))
        if major:
            result.append(JavaInstall(str(c), major))
    # при одинаковой версии: сначала Java, которую скачал сам лаунчер (проверенная Mojang), в конце — 32-битная
    # из «Program Files (x86)» (ей нельзя дать больше ~1,5 ГБ памяти, с модами игра не запустится)
    def rank(j: JavaInstall):
        p = j.path.lower().replace("\\", "/")
        return (j.major, 0 if "/runtime/" in p else 1, 1 if "(x86)" in p else 0)
    return sorted(result, key=rank)


def pick_java(installs: list[JavaInstall], required: int) -> JavaInstall | None:
    """Точное совпадение версии > ближайшая более новая > самая новая из имеющихся."""
    exact = [j for j in installs if j.major == required]
    if exact:
        return exact[0]
    newer = [j for j in installs if j.major > required]
    if newer:
        return newer[0]
    return installs[-1] if installs else None


# ----------------------------------------------------------------------------- Windows-специфичное

def high_priority_flags() -> int:
    """Флаг «выше обычного» приоритета процесса игры (Windows)."""
    return 0x00008000 if is_windows() else 0  # ABOVE_NORMAL_PRIORITY_CLASS


def set_process_priority(process, flag: int) -> None:
    """Меняет приоритет уже запущенного процесса (Windows). Ошибки не важны — приоритет только улучшение."""
    if not is_windows() or process is None:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.c_void_p(int(process._handle)), flag)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def set_gpu_preference(java_path: str, high_performance: bool = True) -> bool:
    """
    Просит Windows запускать Java на мощной (дискретной) видеокарте — то же самое, что
    «Параметры → Дисплей → Графика → Высокая производительность». Актуально для ноутбуков.
    """
    if not is_windows() or not java_path:
        return False
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences")
        winreg.SetValueEx(key, str(Path(java_path)), 0, winreg.REG_SZ,
                          "GpuPreference=2;" if high_performance else "GpuPreference=0;")
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def protect(text: str) -> str:
    """Шифрует токены через Windows DPAPI (расшифровать может только этот пользователь Windows)."""
    if not text or not is_windows():
        return text
    try:
        import base64
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        data = text.encode("utf-8")
        inp = Blob(len(data), ctypes.create_string_buffer(data, len(data)))
        out = Blob()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):  # type: ignore[attr-defined]
            return text
        raw = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)  # type: ignore[attr-defined]
        return "dpapi:" + base64.b64encode(raw).decode()
    except Exception:  # noqa: BLE001
        return text


def unprotect(text: str) -> str:
    if not text or not text.startswith("dpapi:") or not is_windows():
        return text
    try:
        import base64
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        raw = base64.b64decode(text[6:])
        inp = Blob(len(raw), ctypes.create_string_buffer(raw, len(raw)))
        out = Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):  # type: ignore[attr-defined]
            return ""
        data = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)  # type: ignore[attr-defined]
        return data.decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def create_desktop_shortcut() -> Path | None:
    """
    Ярлык «VaultMC» с иконкой на рабочем столе.
    Из .exe — ярлык на .exe; из исходников — на pythonw.exe main.py (без окна консоли).
    """
    icon = resource_path("assets/logo.ico")
    if getattr(sys, "frozen", False):
        target, args, workdir = sys.executable, "", str(Path(sys.executable).parent)
    else:
        main = Path(__file__).resolve().parent.parent / "main.py"
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        branded = exe.with_name("VaultMC.exe")   # копия Python с названием и значком лаунчера (tools/exeicon.py)
        target = str(branded if branded.exists() else pythonw if pythonw.exists() else exe)
        args, workdir = f'"{main}"', str(main.parent)

    if is_windows():
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        try:  # «Рабочий стол» может быть перенесён (OneDrive) — спросим у Windows
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                                 capture_output=True, text=True, timeout=15, creationflags=no_window_flags())
            if out.stdout.strip():
                desktop = Path(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
        link = desktop / f"{APP_NAME}.lnk"
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:NL_LINK);"
              "$s.TargetPath=$env:NL_TARGET;$s.Arguments=$env:NL_ARGS;$s.WorkingDirectory=$env:NL_DIR;"
              "$s.IconLocation=$env:NL_ICON;$s.Save()")
        env = dict(os.environ, NL_LINK=str(link), NL_TARGET=target, NL_ARGS=args, NL_DIR=workdir, NL_ICON=str(icon))
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                                env=env, capture_output=True, timeout=30, creationflags=no_window_flags())
        return link if result.returncode == 0 and link.exists() else None

    if sys.platform.startswith("linux"):
        apps = Path.home() / ".local" / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        entry = (f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nExec={target} {args}\n"
                 f"Path={workdir}\nIcon={resource_path('assets/logo.png')}\nTerminal=false\nCategories=Game;\n")
        link = apps / "vaultmc.desktop"
        link.write_text(entry, encoding="utf-8")
        link.chmod(0o755)
        desktop_dir = Path.home() / "Desktop"
        if desktop_dir.is_dir():
            shutil.copy2(link, desktop_dir / link.name)
        return link
    return None
