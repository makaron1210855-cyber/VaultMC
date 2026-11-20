"""
Облако миров — часть без сети (этап 4 плана 8.0, дизайн docs/world-sync-design.md, номера разделов — оттуда).

Здесь: отпечаток мира, архив и проверенная распаковка, state.json, таблица решений (7.1), замена папки мира
с журналом (7.4). Запросы к серверу — core/worldcloud.py, порядок действий — core/worldengine.py.

Главные правила (раздел 2): локальный мир главный — папка не стирается, а уходит в корзину сборки; «какая копия
новее» решает цепочка версий на сервере, а не часы; переносы папок — только os.rename (shutil.move при неудаче
молча копирует по файлу, оборвётся — в saves останется полмира).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import worlds as W
from .utils import LAUNCHER_HOME, protect, unprotect

SYNC_HOME = LAUNCHER_HOME / "worldsync"
PART_SIZE = 16 << 20           # как на сервере (limits.part_size приходит в list)
MAX_WORLD = 512 << 20
MAX_FILES = 100_000
MAX_REL = 150                  # путь внутри мира; вместе с папкой сборки — в пределах 260 знаков Windows
SYNC_DIRNAME = ".vaultmc-sync"
JOURNAL = "apply.json"


class UnsafeArchive(Exception):
    """Архив мира не прошёл проверку (вредные пути, слишком большой, повреждён)."""


class Interrupted(Exception):
    """Остановили: игру этой сборки запустили, пока шла работа с миром."""


# ============================================================================ отпечаток и сведения о мире
def fingerprint(world: Path) -> str:
    """sha256 от отсортированных строк «путь\\0размер\\0mtime_ns» всех файлов мира, кроме session.lock и кэша
    модов (worlds.world_files). Только stat(), файлы не читаются (раздел 5.5)."""
    rows = []
    for f in W.world_files(world):
        try:
            st = f.stat()
        except OSError:
            continue                              # файл исчез по ходу обхода — это уже «изменился»
        rows.append(f"{f.relative_to(world).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}")
    rows.sort()
    h = hashlib.sha256()
    for row in rows:
        h.update(row.encode("utf-8", "surrogatepass"))
        h.update(b"\n")
    return h.hexdigest()


def level_info(world: Path) -> dict | None:
    """level.dat мира: название, версия, режим. None — level.dat не читается (мир повреждён)."""
    level = world / "level.dat"
    try:
        data = W.read_nbt(level.read_bytes()).get("Data", {})
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    ver = data.get("Version") or {}
    return {
        "title": str(data.get("LevelName") or world.name)[:64],
        "mc": str(ver.get("Name", ""))[:30] if isinstance(ver, dict) else "",
        "data_version": int(data.get("DataVersion", 0) or 0),
        "mode": W.GAME_MODES.get(data.get("GameType", 0), ""),
        "hardcore": bool(data.get("hardcore", 0)),
        "last_played": int(data.get("LastPlayed", 0) or 0),
        "mtime": level.stat().st_mtime,
    }


def world_meta(world: Path, inst) -> dict:
    """meta версии (раздел 5.2): то, что сервер показывает другим ПК игрока."""
    info = level_info(world) or {}
    files = list(W.world_files(world))
    raw = 0
    for f in files:
        try:
            raw += f.stat().st_size
        except OSError:
            pass
    icon = ""
    try:
        data = (world / "icon.png").read_bytes()
        if len(data) <= 9000:                      # base64 ≤ 12 КБ
            import base64
            icon = base64.b64encode(data).decode()
    except OSError:
        pass
    mods = []
    try:
        from .modcheck import read_mod
        for jar in sorted(inst.mods_dir.glob("*.jar"))[:200]:
            m = read_mod(jar)
            if m.ids and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", m.ids[0]):
                mods.append({"id": m.ids[0], "v": str(m.version or "")[:40]})
    except Exception:  # noqa: BLE001  список модов — подсказка, без него тоже работаем
        mods = []
    return {"title": info.get("title") or world.name, "folder": world.name[:64], "mc": info.get("mc", ""),
            "data_version": info.get("data_version", 0), "mode": info.get("mode", ""), "hardcore": info.get("hardcore", False),
            "last_played": info.get("last_played", 0), "raw_size": raw, "files": len(files), "loader": inst.loader,
            "instance": inst.name[:60], "mods": mods, "icon": icon}


def biggest_parts(world: Path, n: int = 3) -> list[tuple[str, int]]:
    """Что больше всего занимает места в мире (для «мир слишком большой»): верхние папки и файлы."""
    sizes: dict[str, int] = {}
    for f in W.world_files(world):
        top = f.relative_to(world).parts[0]
        try:
            sizes[top] = sizes.get(top, 0) + f.stat().st_size
        except OSError:
            pass
    return sorted(sizes.items(), key=lambda kv: -kv[1])[:n]


# ============================================================================ архив
def replace_retry(src: Path, dst: Path, tries: int = 10) -> None:
    """os.replace с повтором: только что записанный файл антивирус на секунду берёт на проверку («Отказано в доступе»)."""
    for n in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(0.2 * (n + 1))


def build_archive(world: Path, out: Path, part_size: int = PART_SIZE, still_ok=lambda: True) -> dict:
    """zip мира (внутри всегда папка world/) в out через временный файл. Возвращает size, sha256, md5 каждой части."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
            for f in sorted(W.world_files(world)):
                if not still_ok():
                    raise Interrupted("игру запустили")
                try:
                    zf.write(f, f"world/{f.relative_to(world).as_posix()}")
                except OSError as exc:            # файл занят или исчез: архив был бы неполным
                    raise OSError(f"не удалось прочитать {f.name}: {exc}") from None
        info = hash_file(tmp, part_size)
        replace_retry(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return info


def hash_file(path: Path, part_size: int = PART_SIZE) -> dict:
    """size, sha256 всего файла и md5 каждой части (md5 части = etag части в R2)."""
    sha, parts, size = hashlib.sha256(), [], 0
    md5 = hashlib.md5()                           # noqa: S324  не защита — контрольная сумма части (etag R2)
    in_part = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            sha.update(chunk)
            size += len(chunk)
            while chunk:
                take = chunk[:part_size - in_part]
                md5.update(take)
                in_part += len(take)
                chunk = chunk[len(take):]
                if in_part == part_size:
                    parts.append(md5.hexdigest())
                    md5, in_part = hashlib.md5(), 0   # noqa: S324
    if in_part:
        parts.append(md5.hexdigest())
    return {"size": size, "sha256": sha.hexdigest(), "md5": parts}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"|?*\\]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def check_member(name: str) -> str | None:
    """Имя записи архива → путь внутри мира (None — запись-папка). Вредное имя — UnsafeArchive (раздел 9):
    всё внутри world/, без абсолютных путей, «..», дисков и потоков NTFS (двоеточие), имён CON/NUL/… с любым
    расширением, точек и пробелов в конце, знаков <>"?*| и кодов 1–31."""
    if not name.startswith("world/"):
        raise UnsafeArchive("в архиве есть файл вне папки мира")
    rel = name[len("world/"):]
    is_dir = rel.endswith("/")
    rel = rel.rstrip("/")
    if not rel:
        return None
    if rel.startswith("/") or _BAD_CHARS.search(rel):
        raise UnsafeArchive("в архиве недопустимое имя файла")
    for part in rel.split("/"):
        if part in ("", ".", "..") or part[-1] in ". " or part.split(".")[0].rstrip().upper() in _RESERVED:
            raise UnsafeArchive("в архиве недопустимое имя файла")
    if len(rel) > MAX_REL:
        raise UnsafeArchive("в архиве слишком длинный путь")
    return None if is_dir else rel


def extract_archive(zip_path: Path, dest: Path, limit: int, still_ok=lambda: True) -> dict:
    """Распаковка в dest (папка мира). limit — сколько байт можно распаковать всего: размерам из заголовков не
    верим, считаем по ходу (защита от zip-бомбы). Возвращает {files, bytes}."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsafeArchive(f"архив не открывается: {exc}") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise UnsafeArchive("в архиве слишком много файлов")
        seen, plan = set(), []
        for info in infos:
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                raise UnsafeArchive("в архиве есть символьная ссылка")
            rel = check_member(info.filename)
            if rel is None:
                continue
            low = rel.lower()
            if low in seen:
                raise UnsafeArchive("в архиве два файла с одним именем")
            seen.add(low)
            plan.append((info, rel))
        if sum(i.file_size for i, _ in plan) > limit:
            raise UnsafeArchive("архив распаковывается в слишком большой объём")
        if len(str(dest)) + 1 + max((len(r) for _, r in plan), default=0) > 255:
            raise UnsafeArchive("путь к файлам мира получился слишком длинным для Windows")
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        total = 0
        for info, rel in plan:
            if not still_ok():
                raise Interrupted("игру запустили")
            out = dest.joinpath(*rel.split("/"))
            if root not in out.resolve().parents:
                raise UnsafeArchive("файл вне папки мира")
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(out, "wb") as dst:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        total += len(chunk)
                        if total > limit:
                            raise UnsafeArchive("архив распаковывается в слишком большой объём")
                        dst.write(chunk)
            except (zipfile.BadZipFile, NotADirectoryError, FileExistsError, IsADirectoryError) as exc:
                raise UnsafeArchive(f"архив повреждён: {exc}") from None
    return {"files": len(plan), "bytes": total}


# ============================================================================ замок игры
def java_pid_alive(pid: int) -> bool:
    """Процесс pid жив и это java/javaw (метка «играем», раздел 7.2)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)         # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
                return False
            buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return "java" in os.path.basename(buf.value).lower()
            return True
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def world_locked(world: Path) -> bool:
    """Игра держит блокировку на session.lock — мир открыт. Не удалось проверить — считаем, что открыт."""
    lock = world / "session.lock"
    if not lock.exists():
        return False
    if sys.platform != "win32":
        return False
    import msvcrt
    try:
        fd = os.open(str(lock), os.O_RDWR)
    except OSError:
        return True
    try:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)


# ============================================================================ state.json
def machine_hash() -> str:
    guid = ""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                guid = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            pass
    # имя компьютера — в хэш: у ПК с одного образа Windows MachineGuid бывает одинаковым
    return hashlib.sha256(f"{guid}|{socket.gethostname()}".encode("utf-8", "replace")).hexdigest()[:16]


def clean_device_name(name: str) -> str:
    """Название ПК вводит игрок: без служебных знаков (оно может попасть в имя папки), до 40 знаков."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip()[:40] or "ПК"


def seal(text: str) -> str:
    return protect(text)


def unseal(text: str) -> str:
    return unprotect(text) if text else ""


class SyncState:
    """%USERPROFILE%\\.vaultmc\\worldsync\\state.json (раздел 5.4). Пишется через временный файл + os.replace, рядом
    .bak; битый файл → .bak, нет и его → «привязок нет» (ничего не удаляем). Все записи — под одним замком."""

    def __init__(self, path: Path | None = None):
        self.path = path or SYNC_HOME / "state.json"
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        data = None
        for p in (self.path, self.path.with_name(self.path.name + ".bak")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("bindings", []), list):
                    data = raw
                    break
            except (OSError, ValueError):
                continue
        data = data or {}
        data.setdefault("v", 1)
        data.setdefault("bindings", [])
        data.setdefault("playing", {})
        dev = data.get("device") if isinstance(data.get("device"), dict) else {}
        machine = machine_hash()
        if not dev.get("id") or dev.get("machine") != machine:
            # папку .vaultmc перенесли на другой ПК (или первый запуск): новое устройство; чужие загрузки не продолжаем
            dev = {"id": secrets.token_hex(16), "name": dev.get("name") or clean_device_name(socket.gethostname()),
                   "machine": machine}
            data["bindings"] = [{**b, "upload": None} for b in data["bindings"] if isinstance(b, dict)]
        data["device"] = dev
        return data

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            if self.path.exists():
                try:
                    shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
                except OSError:
                    pass
            replace_retry(tmp, self.path)

    # ---- устройство
    @property
    def device(self) -> dict:
        return dict(self.data["device"])

    def set_device_name(self, name: str) -> None:
        with self.lock:
            self.data["device"] = {**self.data["device"], "name": clean_device_name(name)}
            self.save()

    # ---- привязки (копия при чтении: менять — только через update)
    def all(self) -> list[dict]:
        with self.lock:
            return [dict(b) for b in self.data["bindings"]]

    def find(self, nick: str, instance: str, folder: str) -> dict | None:
        with self.lock:
            for b in self.data["bindings"]:
                if b["nick"].lower() == nick.lower() and b["instance"] == instance and b["folder"] == folder:
                    return dict(b)
        return None

    def find_folder(self, instance: str, folder: str) -> dict | None:
        """Одна папка — не больше одной привязки, какой бы ни был ник (раздел 5.4)."""
        with self.lock:
            for b in self.data["bindings"]:
                if b["instance"] == instance and b["folder"] == folder:
                    return dict(b)
        return None

    def by_wid(self, nick: str, wid: str) -> list[dict]:
        with self.lock:
            return [dict(b) for b in self.data["bindings"] if b["nick"].lower() == nick.lower() and b["wid"] == wid]

    def for_instance(self, instance: str) -> list[dict]:
        with self.lock:
            return [dict(b) for b in self.data["bindings"] if b["instance"] == instance]

    def for_nick(self, nick: str) -> list[dict]:
        with self.lock:
            return [dict(b) for b in self.data["bindings"] if b["nick"].lower() == nick.lower()]

    def add(self, b: dict) -> dict:
        with self.lock:
            self.data["bindings"].append(dict(b))
            self.save()
            return dict(b)

    def update(self, b: dict, **changes) -> dict | None:
        """Новая запись привязки вместо старой (находим по нику, сборке и папке). None — привязку уже убрали."""
        with self.lock:
            for i, cur in enumerate(self.data["bindings"]):
                if cur["nick"].lower() == b["nick"].lower() and cur["instance"] == b["instance"] and cur["folder"] == b["folder"]:
                    new = {**cur, **changes}
                    self.data["bindings"][i] = new
                    self.save()
                    return dict(new)
        return None

    def remove(self, b: dict) -> None:
        with self.lock:
            self.data["bindings"] = [c for c in self.data["bindings"] if not (
                c["nick"].lower() == b["nick"].lower() and c["instance"] == b["instance"] and c["folder"] == b["folder"])]
            self.save()

    def remove_instance(self, instance: str) -> None:
        with self.lock:
            self.data["bindings"] = [c for c in self.data["bindings"] if c["instance"] != instance]
            self.save()

    def rename_nick(self, old: str, new: str) -> None:
        with self.lock:
            self.data["bindings"] = [{**c, "nick": new} if c["nick"].lower() == old.lower() else c for c in self.data["bindings"]]
            self.save()

    # ---- метка «игра идёт» (7.2)
    def playing(self) -> dict:
        with self.lock:
            return dict(self.data.get("playing") or {})

    def set_playing(self, instance: str = "", pid: int = 0) -> None:
        with self.lock:
            if not instance and not self.data.get("playing"):
                return                                   # метки не было — файл state.json не трогаем (облако выключено)
            self.data["playing"] = {"instance": instance, "pid": int(pid), "started": int(time.time())} if instance else {}
            self.save()


def new_binding(nick: str, instance: str, folder: str, wid: str, owner_tag: str = "") -> dict:
    return {"nick": nick, "instance": instance, "folder": folder, "wid": wid, "base_rid": "", "base_rev": 0, "fp": "",
            "synced_at": 0, "owner_tag": owner_tag, "level_mtime": 0.0, "hold": "", "hold_fp": "", "choose": False,
            "off": False, "paused": "", "err": "", "link": False, "upload": None}


# ============================================================================ таблица решений (7.1)
def decide(*, online: bool, owner_ok: bool, gone: bool, listed: bool, folder_exists: bool, changed: bool,
           relation: str | None, hold: str = "", older: bool = False, choose: bool = False) -> str:
    """Что делать с одним миром. Возвращает: offline | paused_owner | gone | cloud_only | choose | held | upload |
    download | none. Ничего не выбирает по часам компьютеров (правило 2)."""
    if not online or (not gone and not listed):
        return "offline"                     # нет связи или ответ неполный — не повод что-то менять
    if not owner_ok:
        return "paused_owner"
    if gone:
        return "gone"                        # «облачной копии нет» — только кнопка, ничего автоматически (правило 3)
    if not folder_exists:
        return "cloud_only"
    if hold == "conflict":
        return "held"
    if choose or relation == "unknown":
        return "choose"
    if changed:
        if relation == "behind":
            return "choose"
        if older:
            return "choose"                  # «тормоз»: мир на этом ПК старее, чем при прошлой синхронизации
        return "held" if hold == "crash" else "upload"
    return "download" if relation == "behind" else "none"


# ============================================================================ замена папки мира (7.4)
def free_stamp_dir(base: Path, name: str) -> Path:
    return W._free_stamp(lambda st: base / f"{name} {st}")


def sync_dir(inst_root: Path) -> Path:
    return inst_root / SYNC_DIRNAME


def new_dir(inst_root: Path, wid: str) -> Path:
    return sync_dir(inst_root) / f"new-{wid}"


def _write_journal(inst_root: Path, data: dict) -> None:
    path = sync_dir(inst_root) / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(JOURNAL + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    replace_retry(tmp, path)


def _read_journal(inst_root: Path) -> dict | None:
    try:
        data = json.loads((sync_dir(inst_root) / JOURNAL).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _drop_journal(inst_root: Path) -> None:
    (sync_dir(inst_root) / JOURNAL).unlink(missing_ok=True)


def _cache_files(world: Path) -> list[Path]:
    data = world / "data"
    try:
        return [p for p in data.iterdir() if p.is_file() and W.is_cache(Path("data") / p.name)] if data.is_dir() else []
    except OSError:
        return []


def _move_cache(src_world: Path, dst_world: Path, log=print) -> None:
    """Кэш мода (Distant Horizons) в архив не входит — переносим в новую папку, иначе мод заново строит его часами,
    а прежний (8–26 ГБ) неделю лежит в корзине и может забить диск (раздел 7.4)."""
    files = _cache_files(src_world)
    if not files:
        return
    (dst_world / "data").mkdir(parents=True, exist_ok=True)
    for f in files:
        try:
            os.rename(f, dst_world / "data" / f.name)
        except OSError as exc:
            log(f"[Облако] кэш {f.name} не перенесён: {exc}")


def apply_cloud_copy(inst_root: Path, game_dir: Path, folder: str, new_world: Path, *, wid: str, rid: str, rev: int,
                     nick: str, fp_new: str, log=print) -> dict:
    """Заменить saves/<folder> распакованной копией new_world. Порядок: намерение в журнал → кэш → старая папка в
    корзину → новая на место. Оборвалось на любом шаге — recover() доведёт по тому, что на диске.
    Возвращает {folder, rid, rev, fp, level_mtime, trash}."""
    saves = game_dir / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    target = saves / folder
    journal = {"wid": wid, "rid": rid, "rev": rev, "nick": nick, "folder": folder, "fp_new": fp_new, "trash": "",
               "new": str(new_world), "step": "start"}
    _write_journal(inst_root, journal)
    trash_name = ""
    if target.exists():
        _move_cache(target, new_world, log)
        trash_dir = inst_root / "trash"
        stamped = free_stamp_dir(trash_dir, folder).name         # имя — до переноса: оно в журнале
        journal = {**journal, "trash": stamped, "step": "trashing"}
        _write_journal(inst_root, journal)
        trash_name = _rename_to(target, trash_dir / stamped, reason="cloud", rid=rid)
        _write_journal(inst_root, {**journal, "step": "trashed"})
    os.rename(new_world, target)
    _write_journal(inst_root, {**journal, "step": "placed"})
    out = _finish(inst_root, wid, target, rid, rev, fp_new, trash_name)
    return out


def _rename_to(src: Path, dest: Path, reason: str, rid: str) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dest)
    try:
        dest.with_name(dest.name + ".json").write_text(json.dumps({"reason": reason, "rid": rid}), encoding="utf-8")
    except OSError:
        pass
    return dest.name


def _finish(inst_root: Path, wid: str, target: Path, rid: str, rev: int, fp_new: str, trash: str) -> dict:
    _drop_journal(inst_root)
    shutil.rmtree(new_dir(inst_root, wid), ignore_errors=True)
    info = level_info(target) or {}
    return {"folder": target.name, "rid": rid, "rev": rev, "fp": fp_new, "level_mtime": info.get("mtime", 0.0), "trash": trash}


def _done(inst_root: Path, j: dict, target: Path) -> dict:
    out = _finish(inst_root, str(j.get("wid", "")), target, str(j.get("rid", "")), int(j.get("rev", 0)),
                  str(j.get("fp_new", "")), str(j.get("trash", "")))
    return {**out, "wid": str(j.get("wid", "")), "nick": str(j.get("nick", ""))}


def recover(inst_root: Path, game_dir: Path, log=print) -> dict | None:
    """Довести прерванную замену (журнал есть) — по тому, что на диске, а не по последнему шагу:
    - папка мира на месте и распаковка есть → замену не начинали: кэш обратно, распаковку убрать (скачается заново);
    - папки мира нет, распаковка есть → прежняя уже в корзине (или её не было): распаковку на место;
    - папка на месте, распаковки нет: fp == fp_new → наша, записать привязку; иначе не наша — журнал убрать.
    Возвращает результат (как apply_cloud_copy), если мир встал на место; иначе None."""
    j = _read_journal(inst_root)
    if not j:
        return None
    wid, folder = str(j.get("wid", "")), str(j.get("folder", ""))
    if not wid or not folder or "/" in folder or "\\" in folder or folder in (".", ".."):
        _drop_journal(inst_root)
        return None
    target = game_dir / "saves" / folder
    nw = Path(str(j.get("new", "")))
    expected = new_dir(inst_root, wid)
    if nw.parent != expected or not nw.name:        # журнал указывает не туда, куда кладём распаковки — не верим
        _drop_journal(inst_root)
        return None
    if target.exists() and nw.exists():
        _move_cache(nw, target, log)                # кэш мог переехать в распаковку — вернуть
        shutil.rmtree(expected, ignore_errors=True)
        _drop_journal(inst_root)
        return None
    if not target.exists() and nw.exists():
        os.rename(nw, target)
        return _done(inst_root, j, target)
    if target.exists() and not nw.exists():
        if fingerprint(target) == j.get("fp_new"):
            return _done(inst_root, j, target)
        shutil.rmtree(expected, ignore_errors=True)
        _drop_journal(inst_root)
        return None
    _drop_journal(inst_root)                         # ни папки, ни распаковки: терять нечего (прежняя — в корзине, если была)
    shutil.rmtree(expected, ignore_errors=True)
    return None


def free_folder(saves: Path, base: str) -> str:
    """Свободное имя папки мира в saves (без учёта регистра, как в Windows)."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(". ") or "World"
    taken = {p.name.lower() for p in saves.iterdir()} if saves.is_dir() else set()
    name, n = base, 2
    while name.lower() in taken:
        name = f"{base} ({n})"
        n += 1
    return name
