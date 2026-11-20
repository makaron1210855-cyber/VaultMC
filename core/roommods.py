"""Какие моды хозяина нужны гостю комнаты — и установка только их.

Зачем. Мир с модами присылает гостю номера своих звуков, частиц, предметов. Если у гостя такого мода нет, его игра
не может разобрать пакет и сама рвёт соединение — «выкидывает» через несколько минут игры (у хозяина в логе просто
«Отключение»). Ставить гостю всю сборку незачем: моды для FPS, интерфейса и карт у каждого могут быть свои.

Как определяем. Мод нужен гостю, если он работает не только на клиенте (в fabric.mod.json нет "environment": "client")
и сам кладёт что-то в реестры игры: звуки, блоки, предметы, существ, частицы, эффекты, зачарования. Это видно по
коду мода: он обращается к реестру (имена ниже — «intermediary», сверены по таблицам Mojang и Fabric для
1.16.5–1.21.1) и вызывает регистрацию. Проверка приблизительная: мод, который регистрирует необычным способом,
в список не попадёт, а мод-библиотека может попасть зря. Только Fabric и Quilt: Forge и NeoForge сами сверяют моды
при входе и пишут, чего не хватает.
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.request
import zipfile
from pathlib import Path

from .modcheck import read_mod
from .modupdates import _sha1, _title

FABRIC_API_PROJECT = "P7dR8mSH"   # Fabric API на Modrinth
MAX_MODS = 40          # столько записей принимает сервер комнат
SKIP_IDS = {"e4mc", "e4mc_minecraft", "mcwifipnp", "fabric-api", "fabric", "fabricloader", "quilt_loader", "minecraft", "java"}
# Моды, которые точно регистрируют записи, но эвристика по байткоду их не видит (проверено на CreativeCore и PatPat, 1.20.1)
ALWAYS_IDS = {"creativecore", "patpat", "sounds", "fallingleaves"}
# Список на сайте: его можно дополнять без выпуска лаунчера. Нет связи — берём встроенный ALWAYS_IDS.
CATALOG_URL = "https://vaultmc.vaultmc.workers.dev/roomlist.json"
CATALOG_TTL = 3600          # удачный ответ помним час
CATALOG_RETRY = 600         # после сбоя пробуем снова через 10 минут
_catalog: dict = {"at": 0.0, "ids": None, "ok": False}
_ID_RE = re.compile(r"[a-z0-9_\-]{1,64}")


def always_ids() -> set[str]:
    """Моды, которые предлагаем гостю всегда: встроенный список + список с сайта."""
    now = time.time()
    if _catalog["ids"] is None or now - _catalog["at"] >= (CATALOG_TTL if _catalog["ok"] else CATALOG_RETRY):
        _catalog["at"] = now
        try:
            with urllib.request.urlopen(CATALOG_URL, timeout=5) as resp:
                data = json.loads(resp.read(65536).decode("utf-8", "replace"))
            raw = data.get("always") if isinstance(data, dict) else None
            items = [x.strip().lower() for x in (raw if isinstance(raw, list) else []) if isinstance(x, str)]
            ids = {x for x in items if _ID_RE.fullmatch(x)}
            _catalog["ids"], _catalog["ok"] = ids, True
        except (OSError, ValueError, AttributeError):
            _catalog["ids"], _catalog["ok"] = set(), False
    return ALWAYS_IDS | _catalog["ids"]

# Реестры, номера из которых игра шлёт по сети: SOUND_EVENT, FLUID, MOB_EFFECT, BLOCK, ENCHANTMENT, ENTITY_TYPE, ITEM,
# PARTICLE_TYPE, BLOCK_ENTITY_TYPE, MENU, ATTRIBUTE.
_REGISTRY_FIELDS = {
    # class_7923 = BuiltInRegistries (1.19.3+)
    b"class_7923": (b"field_41172", b"field_41173", b"field_41174", b"field_41175", b"field_41176", b"field_41177",
                    b"field_41178", b"field_41180", b"field_41181", b"field_41187", b"field_41190"),
    # class_2378 = Registry (до 1.19.3 сами реестры лежали здесь)
    b"class_2378": (b"field_11156", b"field_11154", b"field_11159", b"field_11146", b"field_11160", b"field_11145",
                    b"field_11142", b"field_11141", b"field_11137", b"field_17429", b"field_23781"),
}
# Registry.register(...) и registerForHolder(...) в разных версиях игры
_REGISTER = re.compile(rb"method_(?:10230|10226|39197|10231|47984|47985)\b")
# Ключи реестров (class_7924 = Registries, до 1.19.3 — поля *_REGISTRY в class_2378): так регистрируют через
# DeferredRegister (Architectury и похожие библиотеки)
_KEY_FIELDS = (b"field_41225", b"field_41270", b"field_41208", b"field_41254", b"field_41265", b"field_41266",
               b"field_41197", b"field_41210", b"field_41255", b"field_41207", b"field_41251",
               b"field_25102", b"field_25103", b"field_25104", b"field_25105", b"field_25106", b"field_25107",
               b"field_25108", b"field_25070", b"field_25073", b"field_25083", b"field_25086")


def _class_registers(data: bytes) -> bool:
    for cls, fields in _REGISTRY_FIELDS.items():
        if cls in data and _REGISTER.search(data) and any(f in data for f in fields):
            return True
    if b"DeferredRegister" in data and (b"class_7924" in data or b"class_2378" in data) and any(f in data for f in _KEY_FIELDS):
        return True
    return False


def _jar_registers(z: zipfile.ZipFile, depth: int = 0) -> bool:
    for name in z.namelist():
        if name.endswith(".class"):
            if _class_registers(z.read(name)):
                return True
        elif name.endswith(".jar") and depth < 2:      # моды внутри мода (META-INF/jars)
            try:
                with zipfile.ZipFile(io.BytesIO(z.read(name))) as inner:
                    if _is_fabric_api(inner):   # Fabric API бывает вложен в чужой мод
                        continue
                    if _jar_registers(inner, depth + 1):
                        return True
            except (zipfile.BadZipFile, OSError, KeyError):
                continue
    return False


def _is_fabric_api(z: zipfile.ZipFile) -> bool:
    try:
        data = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
    except (KeyError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    mod_id = str(data.get("id", ""))
    return mod_id in SKIP_IDS or mod_id.startswith("fabric-")


def _client_only(z: zipfile.ZipFile) -> bool:
    for meta in ("fabric.mod.json", "quilt.mod.json"):
        try:
            data = json.loads(z.read(meta).decode("utf-8", "replace"), strict=False)
        except (KeyError, ValueError):
            continue
        if not isinstance(data, dict):      # испорченный файл мода — не падаем, считаем, что он не клиентский
            continue
        if meta == "fabric.mod.json":
            return str(data.get("environment", "*")).lower() == "client"
        minecraft = data.get("minecraft")
        env = minecraft.get("environment", "*") if isinstance(minecraft, dict) else "*"
        return str(env).lower() == "client"
    return False


def _enabled_jars(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return sorted(f for f in mods_dir.iterdir() if f.is_file() and f.name.endswith(".jar"))


_CACHE: dict = {}


def needed_mods(mods_dir: Path, loader: str, log=print) -> list[dict]:
    """Моды хозяина, которые нужны и гостю: [{"id", "name", "sha1", "size"}]. Результат запоминается, пока папка не менялась."""
    if loader not in ("fabric", "quilt"):
        return []
    jars = _enabled_jars(mods_dir)
    sig = tuple((f.name, f.stat().st_size, int(f.stat().st_mtime)) for f in jars)
    hit = _CACHE.get(str(mods_dir))
    if hit and hit[0] == sig:
        return hit[1]
    out: list[dict] = []
    always = always_ids()
    for jar in jars:
        try:
            info = read_mod(jar)
            mod_id = (info.ids[0] if info.ids else "").strip().lower()
            if not mod_id or mod_id in SKIP_IDS or info.kind not in ("fabric", "quilt"):
                continue
            # environment=client не спасает: такой мод (sounds, fallingleaves) всё равно регистрирует
            # звуки/частицы, и гость без него получает «неизвестные записи реестра» при входе
            if mod_id not in always:
                with zipfile.ZipFile(jar) as z:
                    if not _jar_registers(z):
                        continue
            out.append({"id": mod_id[:64], "name": _title(jar, None)[:60], "ver": _clean_ver(info.version),
                        "sha1": _sha1(jar), "size": jar.stat().st_size})
        except Exception as exc:  # noqa: BLE001 — один битый мод не должен ломать открытие комнаты
            log(f"[Комната] {jar.name}: не прочитался ({exc})")
    out = out[:MAX_MODS]
    _CACHE[str(mods_dir)] = (sig, out)
    return out


_VER_RE = re.compile(r"[0-9A-Za-z.+_\-]{1,40}")


def _clean_ver(value) -> str:
    """Версия мода для показа: только безопасные знаки (она приходит и от других игроков)."""
    text = str(value or "").strip()[:40]
    return text if _VER_RE.fullmatch(text) else ""


def clean_list(mods) -> list[dict]:
    """Список модов, пришедший с сервера (то есть от другого игрока): оставляем только правильные записи."""
    out = []
    for m in mods if isinstance(mods, list) else []:
        if not isinstance(m, dict):
            continue
        mod_id, sha = str(m.get("id", "")).lower(), str(m.get("sha1", "")).lower()
        if re.fullmatch(r"[a-z0-9_\-]{1,64}", mod_id) and re.fullmatch(r"[0-9a-f]{40}", sha):
            size = m.get("size")
            out.append({"id": mod_id, "name": str(m.get("name", "") or mod_id)[:60], "ver": _clean_ver(m.get("ver")),
                        "sha1": sha, "size": int(size) if isinstance(size, (int, float)) and 0 < size < 2 ** 31 else 0})
    return out[:MAX_MODS]


def _guest_mods(mods_dir: Path) -> dict[str, list[tuple[Path, str]]]:
    """id мода → [(файл, версия)] для включённых модов сборки гостя."""
    have: dict[str, list[tuple[Path, str]]] = {}
    for jar in _enabled_jars(mods_dir):
        info = read_mod(jar)
        for i in info.ids:
            if i:
                have.setdefault(i.lower(), []).append((jar, info.version or ""))
    return have


def missing_mods(mods_dir: Path, mods) -> list[dict]:
    """Каких модов хозяина нет в сборке гостя (сверяем по id мода: версия у гостя может быть другой)."""
    mods = clean_list(mods)
    if not mods:
        return []
    have = _guest_mods(mods_dir)
    return [m for m in mods if m["id"] not in have]


def changed_mods(mods_dir: Path, mods) -> list[dict]:
    """Моды, которые у гостя есть, но другой версии, чем у хозяина (файл не тот же по SHA-1).
    Версии реестров у них могут не совпадать — отсюда выкидывание из мира. Запись: мод хозяина + `have_ver` и `have_file` гостя."""
    mods = clean_list(mods)
    if not mods:
        return []
    have = _guest_mods(mods_dir)
    out = []
    for m in mods:
        mine = have.get(m["id"])
        if not mine or any(_sha1(f) == m["sha1"] for f, _ in mine):
            continue
        out.append({**m, "have_ver": _clean_ver(mine[0][1]), "have_file": mine[0][0].name})
    return out


def install_mods(game_dir: Path, game_version: str, loader: str, mods, log=print) -> dict:
    """Ставит гостю моды хозяина — те же файлы, по SHA-1 с Modrinth, вместе с обязательными зависимостями.
    Возвращает {"installed": [названия], "failed": [названия]}; failed — чего на Modrinth нет."""
    from .modsources import MODRINTH_API, ModIndex, ModrinthSource, ModSourceError, sync_index
    from .net import download, post_json

    mods = missing_mods(game_dir / "mods", mods)
    installed, failed = [], []
    if not mods:
        return {"installed": installed, "failed": failed}
    try:
        sync_index(game_dir, "", log)     # чтобы зависимости, которые уже стоят, не скачались второй раз
    except Exception as exc:  # noqa: BLE001
        log(f"[Комната] список модов сборки не обновился: {exc}")
    found = post_json(f"{MODRINTH_API}/version_files", {"hashes": [m["sha1"] for m in mods], "algorithm": "sha1"}) or {}
    source = ModrinthSource()
    seen: set[str] = set()
    for m in mods:
        version = found.get(m["sha1"])
        file = None
        if isinstance(version, dict):
            file = next((f for f in version.get("files", []) if (f.get("hashes") or {}).get("sha1") == m["sha1"]), None)
        name = str((file or {}).get("filename", ""))
        if not file or not str(file.get("url", "")).startswith("https://cdn.modrinth.com/") \
                or not re.fullmatch(r"[^\\/:*?\"<>|]+\.jar", name):
            failed.append(m["name"])
            log(f"[Комната] {m['name']}: такого файла нет на Modrinth — попросите его у друга")
            continue
        try:
            download(file["url"], game_dir / "mods" / name, m["sha1"], file.get("size"))
        except Exception as exc:  # noqa: BLE001
            failed.append(m["name"])
            log(f"[Комната] {m['name']}: не скачался ({exc})")
            continue
        project = str(version.get("project_id", ""))
        if project:
            ModIndex(game_dir).add("modrinth", project, m["name"], name, "mods")   # свежий список: его меняет и установка зависимостей
            seen.add(project)
        installed.append(m["name"])
        log(f"[Комната] Поставлен мод друга: {m['name']}")
        for dep in version.get("dependencies", []):
            if dep.get("dependency_type") == "required" and dep.get("project_id"):
                try:
                    for f in source.install(dep["project_id"], game_version, loader, game_dir, "mod", "", seen):
                        log(f"[Комната] Нужен для него: {f}")
                except ModSourceError as exc:
                    log(f"[Комната] зависимость не поставилась: {exc}")
    if installed and loader == "fabric":   # почти любому моду Fabric нужен Fabric API, а в зависимостях он указан не у всех
        try:
            for f in source.install(FABRIC_API_PROJECT, game_version, loader, game_dir, "mod", "Fabric API", seen):
                log(f"[Комната] Поставлен Fabric API: {f}")
        except Exception as exc:  # noqa: BLE001
            log(f"[Комната] Fabric API не поставился: {exc}")
    return {"installed": installed, "failed": failed}


def update_mods(game_dir: Path, mods, log=print) -> dict:
    """Меняет у гостя моды другой версии на ту же версию, что у хозяина (те же файлы по SHA-1 с Modrinth).
    Старый файл не удаляется, а уходит в папку отката модов — вернуть можно кнопкой «Откат» на вкладке модов.
    Возвращает {"updated": [названия], "failed": [названия]}."""
    from .modsources import MODRINTH_API
    from .modupdates import apply
    from .net import post_json

    changed = changed_mods(game_dir / "mods", mods)     # имя своего файла берём из своей папки, не из присланного списка
    if not changed:
        return {"updated": [], "failed": []}
    found = post_json(f"{MODRINTH_API}/version_files", {"hashes": [m["sha1"] for m in changed], "algorithm": "sha1"}) or {}
    updates, failed = [], []
    for m in changed:
        version = found.get(m["sha1"])
        file = None
        if isinstance(version, dict):
            file = next((f for f in version.get("files", []) if (f.get("hashes") or {}).get("sha1") == m["sha1"]), None)
        name = str((file or {}).get("filename", ""))
        project = str((version or {}).get("project_id", "")) if isinstance(version, dict) else ""
        if not file or not project or not str(file.get("url", "")).startswith("https://cdn.modrinth.com/") \
                or not re.fullmatch(r"[^\/:*?\"<>|]+\.jar", name):
            failed.append(m["name"])
            log(f"[Комната] {m['name']}: версии друга нет на Modrinth — попросите файл у него")
            continue
        updates.append({"file": m["have_file"], "new_file": name, "url": file["url"], "sha1": m["sha1"], "size": file.get("size"),
                        "title": m["name"], "version": str(version.get("version_number", ""))[:40],
                        "source": "modrinth", "project_id": project})
    errors: list[str] = []
    if updates:
        _, errors = apply(game_dir, updates, log)
    bad = {e.split(":", 1)[0] for e in errors}
    updated = [u["title"] for u in updates if u["title"] not in bad]
    failed += [u["title"] for u in updates if u["title"] in bad]
    for u in updates:
        if u["title"] not in bad:
            log(f"[Комната] Мод {u['title']} обновлён до версии друга")
    return {"updated": updated, "failed": failed}
