"""
Миры, серверы и скриншоты сборки.

- Миры: saves/<папка>/level.dat (NBT, gzip) — название, режим, когда играли.
  Резервные копии — zip в <сборка>/backups/, восстановление, копирование в другую сборку.
- Серверы: servers.dat (NBT без сжатия) — тот же список, что в игре «Сетевая игра».
  Пинг — протокол Server List Ping (как сама игра): онлайн, MOTD, версия, иконка.
- Скриншоты: screenshots/*.png.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import os
import re
import shutil
import socket
import struct
import time
import zipfile
from pathlib import Path

# ============================================================================ NBT
TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG, TAG_FLOAT, TAG_DOUBLE, TAG_BYTE_ARRAY, TAG_STRING, TAG_LIST, \
    TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = range(13)


class _Reader:
    def __init__(self, data: bytes):
        self.b = io.BytesIO(data)

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.b.read(size))[0]

    def string(self) -> str:
        n = self.take(">H")
        return self.b.read(n).decode("utf-8", "replace")

    def payload(self, tag: int):
        if tag == TAG_BYTE:
            return self.take(">b")
        if tag == TAG_SHORT:
            return self.take(">h")
        if tag == TAG_INT:
            return self.take(">i")
        if tag == TAG_LONG:
            return self.take(">q")
        if tag == TAG_FLOAT:
            return self.take(">f")
        if tag == TAG_DOUBLE:
            return self.take(">d")
        if tag == TAG_BYTE_ARRAY:
            return self.b.read(self.take(">i"))
        if tag == TAG_STRING:
            return self.string()
        if tag == TAG_LIST:
            inner, n = self.take(">b"), self.take(">i")
            return [self.payload(inner) for _ in range(max(0, n))]
        if tag == TAG_COMPOUND:
            out = {}
            while True:
                t = self.take(">b")
                if t == TAG_END:
                    return out
                key = self.string()          # сначала имя, потом значение (порядок важен)
                out[key] = self.payload(t)
        if tag == TAG_INT_ARRAY:
            return [self.take(">i") for _ in range(self.take(">i"))]
        if tag == TAG_LONG_ARRAY:
            return [self.take(">q") for _ in range(self.take(">i"))]
        raise ValueError(f"неизвестный тег NBT {tag}")  # i18n-skip: внутренняя ошибка разбора


def read_nbt(data: bytes) -> dict:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    r = _Reader(data)
    if r.take(">b") != TAG_COMPOUND:
        raise ValueError("это не NBT")  # i18n-skip: внутренняя ошибка разбора
    r.string()
    return r.payload(TAG_COMPOUND)


def _write_servers(servers: list[dict]) -> bytes:
    """servers.dat: {"servers": [{"name", "ip", "icon"?, "acceptTextures"?}]}."""
    def s(text: str) -> bytes:
        raw = text.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw

    out = bytearray([TAG_COMPOUND]) + s("") + bytes([TAG_LIST]) + s("servers")
    out += bytes([TAG_COMPOUND]) + struct.pack(">i", len(servers))
    for srv in servers:
        for key in ("name", "ip", "icon"):
            if srv.get(key):
                out += bytes([TAG_STRING]) + s(key) + s(str(srv[key]))
        if "acceptTextures" in srv:
            out += bytes([TAG_BYTE]) + s("acceptTextures") + struct.pack(">b", int(srv["acceptTextures"]))
        out += bytes([TAG_END])
    out += bytes([TAG_END])
    return bytes(out)


# ============================================================================ миры
GAME_MODES = {0: "Выживание", 1: "Творческий", 2: "Приключение", 3: "Наблюдатель"}


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def list_worlds(game_dir: Path) -> list[dict]:
    saves = game_dir / "saves"
    if not saves.is_dir():
        return []
    worlds = []
    for d in saves.iterdir():
        level = d / "level.dat"
        if not level.is_file():
            continue
        name, mode, hardcore, version, last = d.name, "", False, "", level.stat().st_mtime
        try:
            data = read_nbt(level.read_bytes()).get("Data", {})
            name = data.get("LevelName") or d.name
            mode = GAME_MODES.get(data.get("GameType", 0), "")
            hardcore = bool(data.get("hardcore", 0))
            version = (data.get("Version") or {}).get("Name", "")
            if data.get("LastPlayed"):
                last = data["LastPlayed"] / 1000
        except Exception:  # noqa: BLE001  повреждённый level.dat — показываем по имени папки
            pass
        icon = d / "icon.png"
        worlds.append({"folder": d.name, "name": name, "mode": "Хардкор" if hardcore else mode, "version": version,
                       "lastPlayed": last, "size": _dir_size(d),
                       "icon": "data:image/png;base64," + base64.b64encode(icon.read_bytes()).decode() if icon.is_file() else ""})
    worlds.sort(key=lambda w: w["lastPlayed"], reverse=True)
    return worlds


def _world_dir(game_dir: Path, folder: str) -> Path:
    path = (game_dir / "saves" / Path(folder).name)
    if not (path / "level.dat").exists():
        raise FileNotFoundError("Мир не найден")
    return path


AUTO_TAG = " auto "      # «Мир auto 2026-11-17 10-00-00.zip» — автокопия после игры (их хранится меньше)


def _free_stamp(path_for) -> Path:
    """Путь с датой-временем, которого ещё нет. Если такой файл уже есть (два действия в одну секунду),
    берём следующую секунду — прежняя копия или мир в корзине не затираются."""
    now = time.time()
    for n in range(3600):
        path = path_for(time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(now + n)))
        if not path.exists():
            return path
    raise OSError("Не удалось подобрать имя для копии мира")


def backup_world(game_dir: Path, folder: str, backups_dir: Path, auto: bool = False) -> Path:
    src = _world_dir(game_dir, folder)
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = _free_stamp(lambda st: backups_dir / f"{src.name}{AUTO_TAG if auto else ' '}{st}.zip")
    tmp = dest.with_suffix(".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
            for f in src.rglob("*"):
                if f.is_file() and f.name != "session.lock":
                    zf.write(f, f"{src.name}/{f.relative_to(src).as_posix()}")
        os.replace(tmp, dest)            # недописанная копия (выключили ПК) не выглядит как целая
    finally:
        tmp.unlink(missing_ok=True)
    _prune_backups(backups_dir, src.name, auto)
    return dest


BACKUPS_PER_WORLD = 15   # старые копии одного мира сверх этого числа удаляются (чтобы папка не росла без конца)
AUTO_PER_WORLD = 5       # автокопий одного мира
AUTO_MAX_BYTES = 2 << 30 # миры больше 2 ГБ автоматически не копируются (долго и много места)


def _prune_backups(backups_dir: Path, world: str, auto: bool = False) -> None:
    tag = re.escape(AUTO_TAG) if auto else " "
    pattern = re.compile(re.escape(world) + tag + r"\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}\.zip")
    mine = sorted((f for f in backups_dir.glob("*.zip") if pattern.fullmatch(f.name)), key=lambda f: f.stat().st_mtime)
    for old in mine[:-(AUTO_PER_WORLD if auto else BACKUPS_PER_WORLD)]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups(backups_dir: Path) -> list[dict]:
    if not backups_dir.is_dir():
        return []
    items = [{"file": f.name, "size": f.stat().st_size, "date": f.stat().st_mtime, "auto": AUTO_TAG in f.name}
             for f in backups_dir.glob("*.zip")]
    return sorted(items, key=lambda b: b["date"], reverse=True)


def auto_backup_changed(game_dir: Path, backups_dir: Path, still_ok=lambda: True, log=print) -> list[str]:
    """После выхода из игры: копируем миры, в которых играли с прошлой автокопии (по времени level.dat).
    still_ok() — можно ли продолжать (игру снова запустили — останавливаемся). Возвращает имена скопированных миров."""
    state_path = backups_dir / "auto_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    done = []
    saves = game_dir / "saves"
    for world in sorted(saves.iterdir()) if saves.is_dir() else []:
        level = world / "level.dat"
        if not level.is_file():
            continue
        stamp = level.stat().st_mtime
        if stamp <= state.get(world.name, 0):
            continue
        if not still_ok():
            break
        if _dir_size(world) > AUTO_MAX_BYTES:
            log(f"[Миры] «{world.name}» больше 2 ГБ — автокопию не делаю, сделайте копию вручную")
            state[world.name] = stamp
            continue
        try:
            backup_world(game_dir, world.name, backups_dir, auto=True)
            state[world.name] = stamp
            done.append(world.name)
        except OSError as exc:
            log(f"[Миры] автокопия «{world.name}» не получилась: {exc}")
    if done or state:
        backups_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return done


# ---------------------------------------------------------------- корзина миров
TRASH_DAYS = 7
_TRASH_RE = re.compile(r"^(?P<name>.+) (?P<date>\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})$")


def trash_world(game_dir: Path, folder: str, trash_dir: Path) -> str:
    """Удалить мир: он не стирается, а уходит в корзину сборки на TRASH_DAYS дней."""
    src = _world_dir(game_dir, folder)
    trash_dir.mkdir(parents=True, exist_ok=True)
    dest = _free_stamp(lambda st: trash_dir / f"{src.name} {st}")
    shutil.move(str(src), str(dest))
    return dest.name


def list_trash(trash_dir: Path) -> list[dict]:
    if not trash_dir.is_dir():
        return []
    out = []
    for d in trash_dir.iterdir():
        m = _TRASH_RE.match(d.name)
        if d.is_dir() and m:
            at = d.stat().st_mtime
            try:
                at = time.mktime(time.strptime(m["date"], "%Y-%m-%d %H-%M-%S"))
            except ValueError:
                pass
            out.append({"entry": d.name, "name": m["name"], "date": at, "size": _dir_size(d),
                        "left_days": max(0, TRASH_DAYS - int((time.time() - at) // 86400))})
    return sorted(out, key=lambda t: t["date"], reverse=True)


def purge_trash(trash_dir: Path, days: int = TRASH_DAYS) -> None:
    for t in list_trash(trash_dir):
        if time.time() - t["date"] > days * 86400:
            shutil.rmtree(trash_dir / t["entry"], ignore_errors=True)


def restore_trash(game_dir: Path, trash_dir: Path, entry: str) -> str:
    src = trash_dir / Path(entry).name
    m = _TRASH_RE.match(src.name)
    if not src.is_dir() or not m:
        raise FileNotFoundError("Этого мира уже нет в корзине")
    saves = game_dir / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    target = _free_name(saves, m["name"])
    shutil.move(str(src), str(target))
    return target.name


def delete_trash(trash_dir: Path, entry: str) -> None:
    path = trash_dir / Path(entry).name
    if path.is_dir() and _TRASH_RE.match(path.name):
        shutil.rmtree(path)


def _free_name(saves: Path, base: str) -> Path:
    target, n = saves / base, 2
    while target.exists():
        target = saves / f"{base} ({n})"
        n += 1
    return target


def restore_backup(game_dir: Path, backups_dir: Path, file: str) -> str:
    """Распаковывает копию рядом (не затирая текущий мир). Возвращает имя новой папки."""
    archive = backups_dir / Path(file).name
    saves = game_dir / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        top = names[0].split("/", 1)[0] if names else archive.stem
        target = _free_name(saves, re.sub(r"[<>:\"/\\|?*]", "_", top))
        root = target.resolve()
        for n in names:
            rel = n.split("/", 1)[1] if "/" in n else n
            out = (target / rel).resolve()
            if root not in out.parents:
                continue                          # защита от путей вне папки
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return target.name


def copy_world(src_game_dir: Path, folder: str, dst_game_dir: Path) -> str:
    src = _world_dir(src_game_dir, folder)
    saves = dst_game_dir / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    target = _free_name(saves, src.name)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("session.lock"))
    return target.name


def delete_world(game_dir: Path, folder: str) -> None:
    """Стереть мир насовсем (без корзины). Для удаления из интерфейса — trash_world."""
    shutil.rmtree(_world_dir(game_dir, folder))


# ============================================================================ серверы
def list_servers(game_dir: Path) -> list[dict]:
    path = game_dir / "servers.dat"
    if not path.is_file():
        return []
    try:
        servers = read_nbt(path.read_bytes()).get("servers", [])
    except Exception:  # noqa: BLE001
        return []
    return [{"name": s.get("name", ""), "ip": s.get("ip", ""), "icon": s.get("icon", "")} for s in servers if s.get("ip")]


def save_servers(game_dir: Path, servers: list[dict]) -> None:
    path = game_dir / "servers.dat"
    old = {}
    if path.is_file():
        try:
            old = {s.get("ip"): s for s in read_nbt(path.read_bytes()).get("servers", [])}
        except Exception:  # noqa: BLE001
            old = {}
    merged = []
    for s in servers:
        prev = old.get(s["ip"], {})
        entry = {"name": s.get("name") or s["ip"], "ip": s["ip"], "icon": s.get("icon") or prev.get("icon", "")}
        if "acceptTextures" in prev:
            entry["acceptTextures"] = prev["acceptTextures"]
        merged.append(entry)
    game_dir.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_suffix(".dat_old"))
    path.write_bytes(_write_servers(merged))


def _varint(n: int) -> bytes:
    out = bytearray()
    n &= 0xFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_varint(sock: socket.socket) -> int:
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("сервер закрыл соединение")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7
        if shift > 35:
            raise ValueError("плохой ответ сервера")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("сервер закрыл соединение")
        buf += chunk
    return bytes(buf)


def _resolve_srv(host: str) -> tuple[str, int] | None:
    """SRV-запись _minecraft._tcp.<host> (без сторонних библиотек: простой DNS-запрос по UDP)."""
    try:
        import random
        qid = random.randint(0, 65535)
        name = f"_minecraft._tcp.{host}"
        q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
        q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00" + struct.pack(">HH", 33, 1)
        servers = ["1.1.1.1", "8.8.8.8"]
        for dns in servers:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.5)
                try:
                    s.sendto(q, (dns, 53))
                    data = s.recv(2048)
                except OSError:
                    continue
            if struct.unpack(">H", data[:2])[0] != qid or not struct.unpack(">H", data[6:8])[0]:
                return None
            pos = 12
            while data[pos]:                        # пропускаем вопрос
                pos += data[pos] + 1
            pos += 5
            # ответ: имя (часто ссылка 0xC0xx), тип, класс, ttl, длина
            pos += 2 if data[pos] & 0xC0 == 0xC0 else len(name) + 2
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if rtype != 33:
                return None
            _prio, _weight, port = struct.unpack(">HHH", data[pos:pos + 6])
            target, p = [], pos + 6
            while data[p]:
                if data[p] & 0xC0 == 0xC0:          # сжатое имя — берём как есть
                    off = struct.unpack(">H", data[p:p + 2])[0] & 0x3FFF
                    while data[off]:
                        target.append(data[off + 1:off + 1 + data[off]].decode())
                        off += data[off] + 1
                    break
                target.append(data[p + 1:p + 1 + data[p]].decode())
                p += data[p] + 1
            return ".".join(target), port
    except Exception:  # noqa: BLE001
        return None
    return None


def _motd_text(desc) -> str:
    if isinstance(desc, str):
        return re.sub(r"§.", "", desc)
    if isinstance(desc, dict):
        return _motd_text(desc.get("text", "")) + "".join(_motd_text(x) for x in desc.get("extra", []))
    if isinstance(desc, list):
        return "".join(_motd_text(x) for x in desc)
    return ""


def ping(address: str, timeout: float = 4.0) -> dict:
    """Server List Ping. {"online", "ping", "players", "max", "motd", "version", "favicon"} или {"online": False, "error"}."""
    address = address.strip()
    host, _, port_s = address.rpartition(":") if address.count(":") == 1 else (address, "", "")
    host = host or address
    port = int(port_s) if port_s.isdigit() else 25565
    if not port_s:
        srv = _resolve_srv(host)
        if srv:
            host, port = srv
    try:
        started = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            raw_host = host.encode("utf-8")
            handshake = _varint(0) + _varint(767) + _varint(len(raw_host)) + raw_host + struct.pack(">H", port) + _varint(1)
            sock.sendall(_varint(len(handshake)) + handshake + _varint(1) + _varint(0))
            _read_varint(sock)                      # длина пакета
            if _read_varint(sock) != 0:
                raise ValueError("неожиданный ответ")
            body = _recv_exact(sock, _read_varint(sock))
            latency = int((time.perf_counter() - started) * 1000)
        data = json.loads(body.decode("utf-8", "replace"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"online": False, "error": "не отвечает" if isinstance(exc, (socket.timeout, TimeoutError))
                else "недоступен" if isinstance(exc, OSError) else "странный ответ"}
    players = data.get("players") or {}
    return {"online": True, "ping": latency, "players": int(players.get("online", 0)), "max": int(players.get("max", 0)),
            "motd": _motd_text(data.get("description", "")).strip()[:200],
            "version": str((data.get("version") or {}).get("name", ""))[:60],
            "favicon": data.get("favicon", "") if str(data.get("favicon", "")).startswith("data:image/") else ""}


# ============================================================================ скриншоты
def list_screenshots(game_dir: Path) -> list[dict]:
    folder = game_dir / "screenshots"
    if not folder.is_dir():
        return []
    shots = [{"file": f.name, "date": f.stat().st_mtime, "size": f.stat().st_size}
             for f in folder.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")]
    return sorted(shots, key=lambda s: s["date"], reverse=True)


def screenshot_data(game_dir: Path, file: str) -> str:
    path = game_dir / "screenshots" / Path(file).name
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
