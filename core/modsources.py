"""
Каталоги: Modrinth и CurseForge — моды, ресурспаки, шейдеры (+ сборки Modrinth, см. modpacks.py).
Установка в один клик с обязательными зависимостями.

Modrinth   — открытый API, ключ не нужен.
CurseForge — нужен бесплатный API-ключ (console.curseforge.com), вводится в настройках.
             Если автор запретил скачивание из сторонних лаунчеров, файл не качается —
             лаунчер предлагает открыть страницу мода (так же делает Prism Launcher).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from .net import download, get_json, post_json

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"
CF_GAME_MINECRAFT = 432
CF_CLASSES = {"mod": 6, "resourcepack": 12, "shader": 6552, "modpack": 4471}
CF_LOADERS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
CF_REQUIRED = 3
CF_HASH_SHA1 = 1

TARGET_DIRS = {"mod": "mods", "resourcepack": "resourcepacks", "shader": "shaderpacks"}
REMOVED_NAME = ".vaultmc_removed.json"   # что игрок удалил сам (ключи «modrinth:…», «curseforge:…», «file:имя»)
SORTS = {  # подпись: (Modrinth index, CurseForge sortField)
    "Популярные": ("downloads", 6),
    "По запросу": ("relevance", 2),
    "Обновлённые": ("updated", 3),
    "Новые": ("newest", 1),
}

OPTIMIZATION_PACK = {
    "sodium": "Sodium — новый движок рендеринга, главный прирост FPS",
    "lithium": "Lithium — оптимизация физики, ИИ мобов и тиков сервера",
    "ferrite-core": "FerriteCore — заметно меньше расход оперативной памяти",
    "immediatelyfast": "ImmediatelyFast — быстрее отрисовка интерфейса и текста",
    "entityculling": "EntityCulling — не рисует сущности, которых не видно",
    "modernfix": "ModernFix — быстрее загрузка игры и меньше памяти",
}
# Тот же набор для Forge/NeoForge: у каждого пункта — моды-заменители (первый подходящий под версию игры и ставится).
OPTIMIZATION_CANDIDATES = {
    "sodium": {"fabric": ["sodium"], "forge": ["embeddium", "xenon-forge"], "neoforge": ["sodium", "embeddium"]},
    "lithium": {"fabric": ["lithium"], "forge": ["canary", "radium"], "neoforge": ["lithium", "radium"]},
    "ferrite-core": {"*": ["ferrite-core"]},
    "immediatelyfast": {"*": ["immediatelyfast"]},
    "entityculling": {"*": ["entityculling"]},
    "modernfix": {"*": ["modernfix"]},
}
OPTIMIZATION_FORGE = {
    "sodium": "Embeddium / Sodium — движок рендеринга, главный прирост FPS (лаунчер выберет подходящий)",
    "lithium": "Canary / Lithium — оптимизация физики, ИИ мобов и тиков сервера",
    "ferrite-core": "FerriteCore — заметно меньше расход оперативной памяти",
    "immediatelyfast": "ImmediatelyFast — быстрее отрисовка интерфейса и текста",
    "entityculling": "EntityCulling — не рисует сущности, которых не видно",
    "modernfix": "ModernFix — быстрее загрузка игры и меньше памяти",
}


def optimization_candidates(role: str, loader: str) -> list[str]:
    options = OPTIMIZATION_CANDIDATES.get(role, {})
    return options.get(loader) or options.get("*") or [role]


def install_optimization_pack(roles: list[str], game_version: str, loader: str, game_dir: Path,
                              log=print) -> tuple[list[str], list[str], str]:
    """Ставит набор оптимизации под загрузчик сборки. Для каждого пункта пробует моды по очереди.
    Возвращает (файлы, пропущенные пункты, причина пропуска для человека)."""
    source = ModrinthSource()
    done, skipped = [], []
    network_error = ""
    mods_dir = game_dir / "mods"
    if mods_dir.is_dir() and any("optifine" in f.name.lower() for f in mods_dir.iterdir() if f.suffix == ".jar"):
        # с OptiFine моды рендера (Sodium/Embeddium/ModernFix и др.) ломают запуск игры
        return [], list(roles), ("в сборке стоит OptiFine — с ним эти моды несовместимы. Выключите OptiFine "
                                 "(вкладка «Моды») и установите набор заново: он даёт больше FPS")
    for role in roles:
        for slug in optimization_candidates(role, loader):
            try:
                files = source.install_by_slug(slug, game_version, loader, game_dir, auto=True)
                done += files
                log(f"[Моды] {slug}: {'установлен' if files else 'уже установлен'}")
                break
            except ModSourceError as exc:
                log(f"[Моды] {slug}: {exc}")
            except Exception as exc:  # noqa: BLE001  сеть / Modrinth недоступен
                network_error = network_error or str(exc)
                log(f"[Моды] {slug}: не удалось связаться с Modrinth — {exc}")
        else:
            skipped.append(role)
    if not skipped:
        reason = ""
    elif network_error and len(skipped) == len(roles):
        reason = "не удалось связаться с Modrinth — проверьте интернет и попробуйте ещё раз"
    else:
        name = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric"}.get(loader, loader)
        reason = (f"их авторы ещё не выпустили версии под Minecraft {game_version} на {name}. "
                  "Ничего не сломалось — просто этих модов пока нет")
        if loader == "forge" and _version_tuple(game_version) >= (1, 20, 2):
            reason += ". Для этой версии игры почти все моды выходят на NeoForge — создайте сборку с NeoForge, " \
                      "там набор оптимизации доступен"
        elif _version_tuple(game_version)[0] >= 26:
            reason += ". Версия игры совсем новая: моды обычно догоняют её за несколько недель — попробуйте позже " \
                      "или выберите версию постарше"
    return done, skipped, reason


def _version_tuple(version: str) -> tuple[int, ...]:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (0,)


TUNNEL_MODS = {
    "e4mc+lan": "e4mc + LAN World Plug-n-Play — без лицензии (рекомендуется)",
    "e4all": "e4all — без лицензии (запасной вариант)",
    "e4mc": "e4mc — только если у всех лицензия",
}
# какие моды Modrinth ставит каждый вариант: туннель (адрес для друзей) + переключатель проверки лицензии
TUNNEL_PACKS = {
    "e4mc+lan": ["e4mc", "mcwifipnp"],
    "e4all": ["e4all"],
    "e4mc": ["e4mc"],
}
# по каким словам в имени файла узнать моды-туннели (чтобы убрать лишние при смене варианта:
# e4all и e4mc вместе не работают, а два переключателя лицензии мешают друг другу)
_TUNNEL_FILE_KEYS = {"e4all": "e4all", "e4mc": "e4mc", "mcwifipnp": "mcwifipnp"}


def install_tunnel(tunnel: str, game_version: str, loader: str, game_dir: Path, log=print) -> tuple[list[str], list[str]]:
    """Ставит мод(ы) комнаты и убирает моды-туннели другого варианта. Возвращает (файлы, пропущенные slug)."""
    pack = TUNNEL_PACKS.get(tunnel) or TUNNEL_PACKS["e4mc+lan"]
    mods = game_dir / "mods"
    if mods.is_dir():
        extra = [key for slug, key in _TUNNEL_FILE_KEYS.items() if slug not in pack]
        for f in mods.iterdir():
            name = f.name.lower()
            if f.is_file() and name.endswith((".jar", ".jar.disabled")) and any(k in name for k in extra):
                try:
                    f.unlink()
                    log(f"[Комната] Убран мод другого варианта: {f.name}")
                except OSError as exc:
                    log(f"[Комната] Не удалось убрать {f.name}: {exc} — закройте игру")
    return install_pack(pack, game_version, loader, game_dir, log)


class ModSourceError(Exception):
    pass


class DistributionBlocked(ModSourceError):
    def __init__(self, title: str, url: str):
        super().__init__(f"Автор «{title}» запретил скачивание из лаунчеров — откройте страницу и скачайте вручную")
        self.url = url


@dataclass
class ModHit:
    source: str
    id: str
    slug: str
    title: str
    description: str
    author: str
    downloads: int
    url: str
    icon_url: str = ""
    follows: int = 0


def _compatible_loaders(loader: str, game_version: str) -> list[str]:
    """NeoForge для 1.20.1 — ответвление Forge: там же работают моды, помеченные только как Forge."""
    if loader == "neoforge" and game_version == "1.20.1":
        return ["neoforge", "forge"]
    return [loader]


class ModIndex:
    """instances/<id>/minecraft/.neon_mods.json — что и откуда установлено."""

    def __init__(self, game_dir: Path):
        self.game_dir = game_dir
        self.path = game_dir / ".neon_mods.json"
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def has(self, source: str, project_id: str) -> bool:
        entry = self.data.get(f"{source}:{project_id}")
        if not entry:
            return False
        path = self.game_dir / entry.get("dir", "mods") / entry["file"]
        return path.exists() or path.with_name(path.name + ".disabled").exists()  # отключённый мод — тоже стоит

    def _paths(self, source: str, project_id: str) -> tuple[Path, Path] | None:
        """(включённый путь, выключенный путь) для проекта из индекса."""
        entry = self.data.get(f"{source}:{project_id}")
        if not isinstance(entry, dict) or not entry.get("file"):
            return None
        name = Path(entry["file"]).name.removesuffix(".disabled")
        on = self.game_dir / entry.get("dir", "mods") / name
        return on, on.with_name(name + ".disabled")

    def is_enabled(self, source: str, project_id: str) -> bool:
        paths = self._paths(source, project_id)
        return bool(paths) and paths[0].exists()

    def set_enabled(self, source: str, project_id: str, enabled: bool) -> bool:
        """Включает/выключает файл (выключенный лежит как «имя.disabled» — игра его не видит). Возвращает итог."""
        paths = self._paths(source, project_id)
        if not paths:
            raise FileNotFoundError("Файл не найден — возможно, его удалили вручную")
        on, off = paths
        if enabled and off.exists() and not on.exists():
            off.rename(on)
        elif not enabled and on.exists():
            if off.exists():
                off.unlink()
            on.rename(off)
        if not on.exists() and not off.exists():
            raise FileNotFoundError("Файл не найден — возможно, его удалили вручную")
        return on.exists()

    def remove(self, source: str, project_id: str) -> list[str]:
        """Удаляет файл проекта из сборки (и выключенный .disabled) и запись в индексе. Возвращает удалённые файлы."""
        entry = self.data.pop(f"{source}:{project_id}", None)
        removed: list[str] = []
        if isinstance(entry, dict) and entry.get("file"):
            path = self.game_dir / entry.get("dir", "mods") / Path(entry["file"]).name
            for candidate in (path, path.with_name(path.name + ".disabled")):
                if candidate.is_file():
                    candidate.unlink()
                    removed.append(candidate.name)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        return removed

    def add(self, source: str, project_id: str, title: str, filename: str, folder: str) -> None:
        self.data[f"{source}:{project_id}"] = {"title": title, "file": filename, "dir": folder}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- моды, которые игрок удалил сам: лаунчер не должен ставить их обратно сам (CustomSkinLoader при запуске,
    # набор оптимизации, зависимости). Явная установка из каталога или «Добавить файл» снимает запрет.
    def removed(self) -> set[str]:
        try:
            data = json.loads((self.game_dir / REMOVED_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        return {str(x) for x in data} if isinstance(data, list) else set()

    def _save_removed(self, keys: set[str]) -> None:
        path = self.game_dir / REMOVED_NAME
        if keys:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sorted(keys), indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def mark_removed(self, *keys: str) -> None:
        self._save_removed(self.removed() | {k.lower() if k.startswith("file:") else k for k in keys if k})

    def unmark_removed(self, *keys: str) -> None:
        wanted = {k.lower() if k.startswith("file:") else k for k in keys if k}
        current = self.removed()
        if current & wanted:
            self._save_removed(current - wanted)

    def is_removed(self, source: str, project_id: str) -> bool:
        return f"{source}:{project_id}" in self.removed()

    def key_for_file(self, filename: str) -> str:
        """Ключ записи индекса для файла (или пусто, если лаунчер этот файл не сам ставил)."""
        name = filename.removesuffix(".disabled")
        for key, entry in self.data.items():
            if isinstance(entry, dict) and Path(entry.get("file", "")).name.removesuffix(".disabled") == name:
                return key
        return ""


def sync_index(game_dir: Path, cf_key: str = "", log=print) -> int:
    """Узнаёт моды, которые лежат в папке, но не записаны в индекс (после импорта сборки, ручного копирования).
    Modrinth — по SHA-1 файла, CurseForge — по отпечатку. Проверенные файлы запоминаются, сеть дёргаем только для новых.
    Возвращает, сколько записей добавлено."""
    import hashlib
    index = ModIndex(game_dir)
    cache_path = game_dir / ".vault_scan.json"
    try:
        scanned: dict = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        scanned = {}
    known = {(e.get("dir", "mods"), e.get("file")) for e in index.data.values() if isinstance(e, dict)}
    todo: dict[str, tuple[str, Path]] = {}
    for folder in TARGET_DIRS.values():
        d = game_dir / folder
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.suffix.lower() not in (".jar", ".zip") or (folder, f.name) in known:
                continue
            key = f"{folder}/{f.name}:{f.stat().st_size}"
            if key in scanned:
                continue
            todo[key] = (folder, f)
    if not todo:
        return 0
    added, online = 0, False
    new_entries: dict[str, dict] = {}

    def sha1_of(path: Path) -> str:  # по кускам: большая сборка не занимает память целиком
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()
    # --- Modrinth: по SHA-1
    sha = {sha1_of(f): key for key, (_, f) in todo.items()}
    try:
        found = post_json(f"{MODRINTH_API}/version_files", {"hashes": list(sha), "algorithm": "sha1"})
        ids = sorted({v["project_id"] for v in found.values()})
        titles = {}
        if ids:
            for pr in get_json(f"{MODRINTH_API}/projects?{urlencode({'ids': json.dumps(ids)})}"):
                titles[pr["id"]] = (pr.get("title") or pr["id"], pr.get("slug", ""))
        for h, version in found.items():
            folder, f = todo[sha[h]]
            title, slug = titles.get(version["project_id"], (f.stem, ""))
            new_entries[f"modrinth:{version['project_id']}"] = {"title": title, "file": f.name, "dir": folder, "slug": slug}
            added += 1
        online = True
    except Exception as exc:  # noqa: BLE001
        log(f"[Моды] Modrinth не ответил при проверке модов: {exc}")
    # --- CurseForge: по отпечатку
    if cf_key:
        try:
            from .packformats import cf_fingerprint
            # считаем отпечатки только для того, что не нашёл Modrinth: это самая медленная часть
            known_now = {(e.get("dir"), e.get("file")) for e in new_entries.values()}
            fps = {cf_fingerprint(todo[key][1].read_bytes()): key for key in todo
                   if (todo[key][0], todo[key][1].name) not in known_now}
            if not fps:
                raise StopIteration
            res = post_json(f"{CURSEFORGE_API}/fingerprints/{CF_GAME_MINECRAFT}", {"fingerprints": list(fps)},
                            headers={"x-api-key": cf_key}).get("data", {})
            for m in res.get("exactMatches", []):
                fp = m.get("file", {}).get("fileFingerprint")
                key = fps.get(fp)
                if key is None:
                    continue
                folder, f = todo[key]
                mod_id = m.get("file", {}).get("modId") or m.get("id")
                new_entries[f"curseforge:{mod_id}"] = {"title": m.get("file", {}).get("displayName") or f.stem,
                                                       "file": f.name, "dir": folder}
                added += 1
        except StopIteration:
            pass
        except Exception as exc:  # noqa: BLE001
            log(f"[Моды] CurseForge не ответил при проверке модов: {exc}")
    if online:  # без связи не запоминаем — проверим в следующий раз
        scanned.update({key: 1 for key in todo})
    if added:
        # перечитываем индекс: пока шла проверка, мог установиться ещё какой-то мод — его запись не теряем
        fresh = ModIndex(game_dir)
        for k, v in new_entries.items():
            fresh.data.setdefault(k, v)
        fresh.path.write_text(json.dumps(fresh.data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        cache_path.write_text(json.dumps(scanned), encoding="utf-8")
    except OSError:
        pass
    return added


# ============================================================================ Modrinth

class ModrinthSource:
    name, title = "modrinth", "Modrinth"

    def search(self, query: str, game_version: str, loader: str, project_type: str = "mod",
               sort: str = "Популярные", offset: int = 0, limit: int = 20) -> tuple[list[ModHit], int]:
        facets = [[f"project_type:{project_type}"]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        if project_type == "mod" and loader:
            facets.append([f"categories:{x}" for x in _compatible_loaders(loader, game_version)])
        if project_type == "modpack":  # лаунчер ставит сборки на Fabric, Forge и NeoForge
            facets.append(["categories:fabric", "categories:forge", "categories:neoforge"])
        index = SORTS.get(sort, SORTS["Популярные"])[0]
        params = {"query": query, "facets": json.dumps(facets), "limit": limit, "offset": offset,
                  "index": index if (query or index != "relevance") else "downloads"}
        data = get_json(f"{MODRINTH_API}/search?{urlencode(params)}")
        hits = [ModHit("modrinth", h["project_id"], h.get("slug", ""), h.get("title", ""), h.get("description", ""),
                       h.get("author", ""), h.get("downloads", 0),
                       f"https://modrinth.com/{project_type}/{h.get('slug', '')}", h.get("icon_url") or "",
                       h.get("follows", 0))
                for h in data.get("hits", [])]
        return hits, data.get("total_hits", len(hits))

    def install(self, project_id: str, game_version: str, loader: str, game_dir: Path, project_type: str = "mod",
                title: str = "", _seen: set[str] | None = None, auto: bool = False) -> list[str]:
        seen = _seen if _seen is not None else set()
        if project_id in seen:
            return []
        seen.add(project_id)
        index = ModIndex(game_dir)
        if index.has("modrinth", project_id):
            return []
        if auto and index.is_removed("modrinth", project_id):
            raise ModSourceError(f"«{title or project_id}» вы удалили сами — не ставлю его снова (установить можно из каталога)")
        query = {"game_versions": json.dumps([game_version])}
        if project_type == "mod":
            query["loaders"] = json.dumps(_compatible_loaders(loader, game_version))
        versions = get_json(f"{MODRINTH_API}/project/{quote(project_id)}/version?{urlencode(query)}")
        if not versions:
            raise ModSourceError(f"«{title or project_id}»: нет версии для {game_version}" +
                                 (f" ({loader})" if project_type == "mod" else ""))
        version = versions[0]
        if not title:  # зависимость — узнаём человеческое название
            try:
                title = get_json(f"{MODRINTH_API}/project/{quote(project_id)}").get("title", project_id)
            except Exception:  # noqa: BLE001
                title = project_id
        file = next((f for f in version["files"] if f.get("primary")), version["files"][0])
        folder = TARGET_DIRS.get(project_type, "mods")
        download(file["url"], game_dir / folder / file["filename"], file.get("hashes", {}).get("sha1"), file.get("size"))
        index.add("modrinth", project_id, title, file["filename"], folder)
        installed = [file["filename"]]
        for dep in version.get("dependencies", []):
            if dep.get("dependency_type") == "required" and dep.get("project_id"):
                try:
                    installed += self.install(dep["project_id"], game_version, loader, game_dir, "mod", "", seen, auto)
                except ModSourceError:
                    pass
        return installed

    def install_by_slug(self, slug: str, game_version: str, loader: str, game_dir: Path, auto: bool = False) -> list[str]:
        project = get_json(f"{MODRINTH_API}/project/{quote(slug)}")
        return self.install(project["id"], game_version, loader, game_dir, "mod", project.get("title", slug), None, auto)


# ============================================================================ CurseForge

class CurseForgeSource:
    name, title = "curseforge", "CurseForge"

    def __init__(self, api_key: str):
        if not api_key:
            raise ModSourceError("Для CurseForge нужен бесплатный API-ключ: Настройки → Каталоги")
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    def _get(self, path: str, params: dict | None = None):
        return get_json(f"{CURSEFORGE_API}{path}" + (f"?{urlencode(params)}" if params else ""), headers=self.headers)

    def search(self, query: str, game_version: str, loader: str, project_type: str = "mod",
               sort: str = "Популярные", offset: int = 0, limit: int = 20) -> tuple[list[ModHit], int]:
        params = {"gameId": CF_GAME_MINECRAFT, "classId": CF_CLASSES[project_type], "searchFilter": query,
                  "sortField": SORTS.get(sort, SORTS["Популярные"])[1], "sortOrder": "desc",
                  "index": offset, "pageSize": limit}
        if game_version:
            params["gameVersion"] = game_version
        if project_type == "mod" and loader in CF_LOADERS:
            params["modLoaderType"] = CF_LOADERS[loader]
        data = self._get("/mods/search", params)
        hits = [ModHit("curseforge", str(m["id"]), m.get("slug", ""), m.get("name", ""), m.get("summary", ""),
                       (m.get("authors") or [{}])[0].get("name", ""), int(m.get("downloadCount", 0)),
                       m.get("links", {}).get("websiteUrl", ""), (m.get("logo") or {}).get("thumbnailUrl", ""),
                       int(m.get("thumbsUpCount", 0)))
                for m in data.get("data", [])]
        # CurseForge отдаёт не больше 10 000 результатов
        return hits, min(data.get("pagination", {}).get("totalCount", len(hits)), 10_000)

    def install(self, project_id: str, game_version: str, loader: str, game_dir: Path, project_type: str = "mod",
                title: str = "", _seen: set[str] | None = None, auto: bool = False) -> list[str]:
        seen = _seen if _seen is not None else set()
        if project_id in seen:
            return []
        seen.add(project_id)
        index = ModIndex(game_dir)
        if index.has("curseforge", project_id):
            return []
        if auto and index.is_removed("curseforge", project_id):
            raise ModSourceError(f"мод {project_id} вы удалили сами — не ставлю его снова (установить можно из каталога)")
        params = {"gameVersion": game_version, "pageSize": 30}
        if project_type == "mod" and loader in CF_LOADERS:
            params["modLoaderType"] = CF_LOADERS[loader]
        files = self._get(f"/mods/{project_id}/files", params).get("data", [])
        mod = self._get(f"/mods/{project_id}").get("data", {}) if not title or not files else {}
        title = title or mod.get("name", project_id)
        if not files:
            raise ModSourceError(f"«{title}»: нет файла для {game_version}")
        file = max(files, key=lambda f: f.get("fileDate", ""))
        if not file.get("downloadUrl"):
            mod = mod or self._get(f"/mods/{project_id}").get("data", {})
            raise DistributionBlocked(title, mod.get("links", {}).get("websiteUrl", "https://www.curseforge.com/minecraft"))
        sha1 = next((h["value"] for h in file.get("hashes", []) if h.get("algo") == CF_HASH_SHA1), None)
        folder = TARGET_DIRS.get(project_type, "mods")
        download(file["downloadUrl"], game_dir / folder / file["fileName"], sha1, file.get("fileLength"))
        index.add("curseforge", project_id, title, file["fileName"], folder)
        installed = [file["fileName"]]
        for dep in file.get("dependencies", []):
            if dep.get("relationType") == CF_REQUIRED:
                try:
                    installed += self.install(str(dep["modId"]), game_version, loader, game_dir, "mod", "", seen)
                except ModSourceError:
                    pass
        return installed


def _cf_download_modpack(self, project_id: str, dest_dir: Path, log=print) -> Path:
    """Скачивает архив сборки CurseForge (последний релиз) во временную папку."""
    files = self._get(f"/mods/{project_id}/files", {"pageSize": 20}).get("data", [])
    mod = self._get(f"/mods/{project_id}").get("data", {})
    title = mod.get("name", project_id)
    if not files:
        raise ModSourceError(f"«{title}»: у сборки нет файлов")
    # предпочитаем релизы (releaseType 1), затем самый свежий
    file = max(files, key=lambda f: (f.get("releaseType") == 1, f.get("fileDate", "")))
    if not file.get("downloadUrl"):
        raise DistributionBlocked(title, mod.get("links", {}).get("websiteUrl", "https://www.curseforge.com/minecraft"))
    dest = dest_dir / file["fileName"]
    log(f"[Сборка] Скачиваю {file['fileName']}...")
    download(file["downloadUrl"], dest, None, file.get("fileLength"))
    return dest


CurseForgeSource.download_modpack = _cf_download_modpack


def make_source(name: str, curseforge_key: str = ""):
    return CurseForgeSource(curseforge_key) if name == "curseforge" else ModrinthSource()


def install_pack(slugs: list[str], game_version: str, loader: str, game_dir: Path, log=print) -> tuple[list[str], list[str]]:
    """Ставит набор модов Modrinth по slug. Возвращает (файлы, пропущенные slug)."""
    source = ModrinthSource()
    done, skipped = [], []
    for slug in slugs:
        try:
            files = source.install_by_slug(slug, game_version, loader, game_dir)
            done += files
            log(f"[Моды] {slug}: {'установлен' if files else 'уже установлен'}")
        except Exception as exc:  # noqa: BLE001
            skipped.append(slug)
            log(f"[Моды] {slug}: пропущен — {exc}")
    return done, skipped
