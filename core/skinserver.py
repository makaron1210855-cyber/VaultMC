"""
Сервер скинов VaultMC (server/worker.js на Cloudflare) и мод CustomSkinLoader в игре.

Как другие игроки видят ваш скин и плащ:
  1. лаунчер загружает скин/плащ на сервер под вашим ником (ник занимается ключом, который хранится на этом ПК);
  2. в каждую сборку с модами (Fabric / Forge / NeoForge) лаунчер сам ставит CustomSkinLoader и пишет ему
     в настройки адрес сервера — мод в игре загружает скины всех игроков по их никам.
  Итог: все, кто играет через VaultMC в сборке с модами, видят скины и плащи друг друга (и по сети, и на серверах).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
import time

from .net import HttpError, get_json, post_json
from .utils import LAUNCHER_HOME, protect, unprotect

TOKENS = LAUNCHER_HOME / "skinserver.json"
CSL_SLUG = "customskinloader"
CSL_ENTRY_NAME = "VaultMC"


class SkinServerError(Exception):
    """status: код ответа сервера (0 — нет связи); code: 'auth' (ключ не подошёл) | 'taken' (ник занят) | ''."""

    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


def server_url() -> str:
    from . import keys
    return (getattr(keys, "SKIN_SERVER", "") or "").rstrip("/")


# ----------------------------------------------------------------------------- ключи ников
# Ник на сервере VaultMC защищён ключом. У оффлайн-аккаунта ключ получается из ника и пароля, поэтому он
# одинаковый на любом компьютере: ввёл тот же ник и пароль — это снова ты (друзья, скин, статус).
# Раньше ключ был случайным и жил только в файле на одном ПК; такой ключ лаунчер меняет на «ключ из пароля»
# при следующем вводе пароля (сервер принимает замену, пока лаунчер помнит прежний ключ — он лежит под «ник:old»).
def _tokens() -> dict:
    try:
        data = json.loads(TOKENS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_tokens(data: dict) -> None:
    TOKENS.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKENS.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, TOKENS)


# Ключ ника создаётся и регистрируется под замком: при входе в аккаунт его одновременно запрашивают
# несколько потоков (занять ник + статус «в сети»). Без замка каждый придумывал свой ключ, сервер запоминал
# один, а в файл попадал другой — и ник навсегда становился «занят другим игроком».
_lock = threading.RLock()


def password_token(name: str, password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"vaultmc-nick:" + name.lower().encode("utf-8"),
                               200_000).hex()


def set_password(name: str, password: str, replace: bool = False) -> None:
    """Игрок ввёл пароль оффлайн-аккаунта (вход, регистрация, смена пароля) — ключ ника теперь из пароля.
    replace=True — пароль сменили: на сервере прежние ключи (и старый пароль) перестанут действовать."""
    with _lock:
        data = _tokens()
        key = name.lower()
        new = password_token(name, password)
        current = unprotect(data[key]) if data.get(key) else ""
        if current == new:
            return
        if current and not data.get(key + ":old"):   # «:old» уже есть — прошлая замена ещё не дошла до сервера, бережём его
            data[key + ":old"] = protect(current)
        if replace:
            data[key + ":replace"] = True
        data[key] = protect(new)
        _save_tokens(data)
        _registered.discard(key)
        _active.pop(key, None)


def _token_for(name: str) -> str:
    with _lock:
        key = name.lower()
        if key in _active:
            return _active[key]
        data = _tokens()
        raw = data.get(key)
        if raw:
            return unprotect(raw)
        token = secrets.token_hex(32)          # аккаунт без пароля (Ely.by): случайный ключ, ник подтверждает Ely.by
        data[key] = protect(token)
        _save_tokens(data)
        return token


def _call(path: str, payload: dict) -> dict:
    url = server_url()
    if not url:
        raise SkinServerError("Сервер VaultMC не настроен")
    try:
        return post_json(f"{url}{path}", payload, timeout=15)
    except HttpError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        raise SkinServerError(body.get("error") or f"Сервер VaultMC ответил {exc.status}", exc.status,
                              str(body.get("code") or "")) from None
    except OSError as exc:
        raise SkinServerError(f"Нет связи с сервером VaultMC: {exc}") from None


# Кто может доказать, что ник принадлежит игроку Ely.by: функция ник → serverId (или пусто).
# Её задаёт backend: ищет среди аккаунтов лаунчера аккаунт Ely.by с таким ником.
ELY_PROVER = None
_registered: set[str] = set()
_active: dict[str, str] = {}       # ник → ключ, которым работаем в этом запуске, если новый ключ сервер пока не принял


def ensure_registered(name: str, prove: bool = True) -> str:
    """Занимает ник на сервере (или подтверждает, что он наш). Возвращает ключ, которым дальше подписывать запросы.
    prove=False — не доказывать ник аккаунтом Ely.by (облако миров для оффлайн-аккаунта: иначе фоновый запрос с 409
    забрал бы ник аккаунтом Ely.by с тем же именем и увёл бы чужие облачные миры, раздел 4.2 дизайна)."""
    with _lock:
        key = name.lower()
        token = _token_for(name)
        if key in _registered:
            return token
        data = _tokens()
        old = unprotect(data[key + ":old"]) if data.get(key + ":old") else ""
        payload = {"name": name, "token": token, **({"old": old} if old and old != token else {}),
                   **({"replace": True} if old and data.get(key + ":replace") else {})}
        try:
            _call("/api/register", payload)
        except SkinServerError as exc:
            if not exc.status:                 # нет связи — не делаем выводов, попробуем в следующий раз
                raise
            proof = ELY_PROVER(name) if ELY_PROVER and prove else ""
            proven = False
            if proof:
                try:
                    _call("/api/register", {**payload, "ely": proof})
                    proven = True
                except SkinServerError:
                    pass
            if not proven:
                if not old or old == token:
                    raise
                # сервер не принял новый ключ (например, сервер ещё старой версии) — работаем прежним, он действует
                _call("/api/register", {"name": name, "token": old})
                _active[key] = old
                _registered.add(key)
                return old
        if old:                                # новый ключ принят — прежний больше не нужен
            data = _tokens()
            data.pop(key + ":old", None)
            data.pop(key + ":replace", None)
            _save_tokens(data)
        _registered.add(key)
        return token


def _authed(name: str, path: str, payload: dict, prove: bool = True) -> dict:
    """Запрос от имени ника. Если сервер не узнал ключ (ник подтвердили с другого ПК) — регистрируемся заново и повторяем."""
    token = ensure_registered(name, prove)
    try:
        return _call(path, {"name": name, "token": token, **payload})
    except SkinServerError as exc:
        # повторяем, только если сервер не узнал ключ ника (code 'auth'; у старого сервера кода нет),
        # а не на любой отказ — «вы не в друзьях» тоже 403, и повтор только тратил запросы
        if exc.status != 403 or exc.code not in ("auth", ""):
            raise
        with _lock:
            _registered.discard(name.lower())
            _active.pop(name.lower(), None)
        token = ensure_registered(name, prove)
        return _call(path, {"name": name, "token": token, **payload})


# ----------------------------------------------------------------------------- код восстановления, смена ника
RECOVERY_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без 0/O и 1/I — чтобы не путали при переписывании


def make_recovery_code() -> str:
    """16 знаков (80 бит), группами по 4: «K7XQ-2M9P-4HTR-8WVD»."""
    raw = "".join(secrets.choice(RECOVERY_CHARS) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def set_recovery(name: str, code: str) -> None:
    """Запомнить на сервере новый код восстановления (сервер хранит только его хэш). Прежний код перестаёт действовать."""
    _authed(name, "/api/recovery", {"code": code})


def _store_token(name: str, token: str) -> None:
    with _lock:
        data = _tokens()
        key = name.lower()
        for suffix in ("", ":old", ":replace"):
            data.pop(key + suffix, None)
        data[key] = protect(token)
        _save_tokens(data)
        _registered.add(key)
        _active.pop(key, None)


def recover(name: str, code: str, password: str) -> None:
    """Забыли пароль: код восстановления + новый пароль. На сервере у ника остаётся только новый ключ."""
    token = password_token(name, password)
    _call("/api/register", {"name": name, "token": token, "recovery": code})
    _store_token(name, token)


def rename(old: str, new: str, password: str) -> None:
    """Смена ника на сервере: друзья, скин и код восстановления переезжают. Ключ нового ника — из пароля и нового ника."""
    token = password_token(new, password)
    _authed(old, "/api/rename", {"new": new, "newToken": token})
    with _lock:
        data = _tokens()
        for suffix in ("", ":old", ":replace"):
            data.pop(old.lower() + suffix, None)
        _save_tokens(data)
        _registered.discard(old.lower())
        _active.pop(old.lower(), None)
    _store_token(new, token)


def download_skin(name: str) -> bytes:
    """Скин ника с сервера VaultMC (PNG) или пусто, если его там нет."""
    import urllib.request
    url = server_url()
    if not url:
        return b""
    req = urllib.request.Request(f"{url}/skins/{name}.png", headers={"User-Agent": "VaultMC"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read(512 * 1024)
    except OSError:
        return b""
    return data if data.startswith(b"\x89PNG") else b""


def upload(name: str, kind: str, png: bytes, slim: bool = False) -> None:
    _authed(name, "/api/upload", {"kind": kind, "slim": slim, "png": base64.b64encode(png).decode()})


def remove(name: str, kind: str) -> None:
    _authed(name, "/api/remove", {"kind": kind})


def friends(name: str, action: str = "list", other: str = "") -> dict:
    return _authed(name, "/api/friends", {"action": action, "other": other})


def set_presence(name: str, state: dict | None) -> None:
    _authed(name, "/api/presence", {"status": state})


def share_pack(name: str, pack: dict, public: bool = False) -> str:
    body: dict = {"pack": pack}
    if public:
        body["public"] = True
    return _authed(name, "/api/share", body).get("code", "")


def get_pack(code: str) -> dict:
    url = server_url()
    if not url:
        raise SkinServerError("Сервер VaultMC не настроен")
    try:
        data = get_json(f"{url}/api/share/{code}", timeout=12, attempts=2)
    except HttpError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        raise SkinServerError(body.get("error") or "Код не найден", exc.status) from None
    except OSError as exc:
        raise SkinServerError(f"Нет связи с сервером VaultMC: {exc}") from None
    return data


def room(name: str, action: str, code: str = "", info: dict | None = None) -> dict:
    """Комната по коду: open (→ code) | update | close."""
    return _authed(name, "/api/room", {"action": action, "code": code, "info": info or {}})


def lookup_room(name: str, code: str) -> dict:
    """Поиск комнаты от имени игрока (сервер 6.4.20+): хозяин, заблокировавший игрока, для него «комнаты нет».
    Старый сервер ответит «неизвестное действие» — тогда вызывающий откатывается на get_room."""
    return _authed(name, "/api/room", {"action": "lookup", "code": code})


def get_room(code: str) -> dict:
    """К кому и в какую версию заходить по коду комнаты. Не требует аккаунта."""
    url = server_url()
    if not url:
        raise SkinServerError("Сервер VaultMC не настроен")
    try:
        return get_json(f"{url}/api/room/{code}", timeout=10, attempts=2)
    except HttpError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        raise SkinServerError(body.get("error") or "Комната не найдена", exc.status, str(body.get("code") or "")) from None
    except OSError as exc:
        raise SkinServerError(f"Нет связи с сервером VaultMC: {exc}") from None


def p2p(name: str, action: str, **extra) -> dict:
    """Обмен приглашениями для прямого подключения: offer | inbox | answer | poll."""
    return _authed(name, "/api/p2p", {"action": action, **extra})


def turn_servers(name: str) -> list:
    """Временные данные ретранслятора TURN для прямого подключения (пусто — сервер не настроен или недоступен)."""
    try:
        return _authed(name, "/api/turn", {}).get("iceServers") or []
    except Exception:  # noqa: BLE001
        return []


def worlds_status() -> dict | None:
    """Сервер умеет облако миров: {v, max_world, part_size}. None — нет (старый сервер, нет R2, нет связи)."""
    url = server_url()
    if not url:
        return None
    try:
        info = get_json(f"{url}/api/status", timeout=6, attempts=1).get("worlds")
    except Exception:  # noqa: BLE001
        return None
    return info if isinstance(info, dict) and info.get("v") == 1 else None


def status() -> bool:
    url = server_url()
    if not url:
        return False
    try:
        return bool(get_json(f"{url}/api/status", timeout=6, attempts=1).get("ok"))
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------------- CustomSkinLoader в сборке
def configure_csl(game_dir: Path) -> None:
    """Пишет/дополняет CustomSkinLoader.json: наш сервер — первым, дальше Ely.by и Mojang."""
    url = server_url()
    if not url:
        return
    path = game_dir / "CustomSkinLoader" / "CustomSkinLoader.json"
    # CustomSkinAPI: адрес картинки содержит её хэш — после «Сохранить» игра берёт новый скин, а не старый из кэша.
    # Legacy (по нику) — запасной путь, если сервер ещё старой версии.
    ours = [{"name": CSL_ENTRY_NAME, "type": "CustomSkinAPI", "root": url + "/csl/"},
            {"name": CSL_ENTRY_NAME + "Legacy", "type": "Legacy", "checkPNG": False, "root": url + "/", "model": "auto"}]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        data = {"enable": True, "loadlist": [{"name": "ElyBy", "type": "ElyByAPI", "root": "http://skinsystem.ely.by/textures/"},
                                             {"name": "Mojang", "type": "MojangAPI"}]}
    mine = {e["name"] for e in ours}
    loadlist = [e for e in data.get("loadlist", []) if isinstance(e, dict) and e.get("name") not in mine]
    data["loadlist"] = ours + loadlist
    data["enable"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_csl(game_dir: Path, game_version: str, loader: str, log=print) -> bool:
    """Ставит CustomSkinLoader в сборку с модами (если ещё нет) и настраивает его. True — всё готово."""
    if loader not in ("fabric", "forge", "neoforge") or not server_url():
        return False
    from .modsources import ModIndex, ModrinthSource
    mods = game_dir / "mods"
    present = mods.is_dir() and any("customskinloader" in f.name.lower() for f in mods.iterdir())
    if not present and any(k.startswith("file:customskinloader") for k in ModIndex(game_dir).removed()):
        return False          # игрок сам удалил CustomSkinLoader — не ставим его обратно при каждом запуске
    if not present:
        failed = game_dir / ".vaultmc-csl-failed"
        try:   # недавно не получилось (нет версии под эту игру / нет сети) — не тормозим каждый запуск
            if time.time() - failed.stat().st_mtime < 24 * 3600:
                return False
        except OSError:
            pass
        try:
            files = ModrinthSource().install_by_slug(CSL_SLUG, game_version, loader, game_dir, auto=True)
            log(f"[Скины] CustomSkinLoader установлен: {', '.join(files) or 'уже был'}")
            failed.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001  нет версии под эту игру / нет сети — игра запустится и так
            log(f"[Скины] Не удалось поставить CustomSkinLoader: {exc}. Скины других игроков будут стандартные")
            try:
                failed.write_text(str(exc)[:300], encoding="utf-8")
            except OSError:
                pass
            return False
    configure_csl(game_dir)
    return True
