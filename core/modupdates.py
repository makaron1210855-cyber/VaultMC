"""
Обновления модов: какие моды из папки mods можно обновить под эту версию игры и загрузчик.

Modrinth — по SHA-1 файла (POST /version_files/update, сразу для всех модов).
CurseForge — по отпечатку файла (POST /fingerprints/432), для модов, которых нет на Modrinth;
  в ответе есть latestFiles — из них берём самый свежий файл под нашу версию игры и загрузчик.
Выключенные моды (.jar.disabled) обновляются и остаются выключенными.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .modsources import CURSEFORGE_API, MODRINTH_API, ModIndex, _compatible_loaders
from .net import download, post_json

CF_LOADER_NAMES = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}
CF_HASH_SHA1 = 1


@dataclass
class ModUpdate:
    file: str            # текущий файл (как лежит в папке, может быть .disabled)
    title: str
    source: str          # modrinth | curseforge
    project_id: str
    new_file: str
    url: str
    sha1: str
    size: int
    version: str         # номер новой версии (для показа)
    old_version: str = ""    # установленная версия (из самого файла или его названия)
    major: bool = False      # крупное обновление: номер версии «ломающе» вырос (2.x → 3.x, у 0.x — 0.5 → 0.6)
    version_id: str = ""     # id версии (Modrinth) или файла (CurseForge) — для «Что нового»


def core_version(text: str, game_version: str = "") -> tuple | None:
    """Номер версии мода из строки («3.0.0-fabric-1.21.1», «0.6.13+mc1.21.1», «reimagined-intro-2.1.0-fabric.jar»).
    Версию игры из строки убираем, чтобы «+1.21.1» не приняли за версию мода. None — номер не нашли."""
    t = text or ""
    if game_version:
        t = t.replace(game_version, " ")
    t = re.sub(r"(?i)(?:mc|minecraft)[-_ ]?\d+(?:\.\d+)+", " ", t)
    t = re.sub(r"(?<=[+\-_])1\.(?:1[6-9]|2\d)(?:\.\d+)?(?![\d.])", " ", t)     # «+1.20.4» — это версия игры
    m = re.search(r"(?<![\d.])(\d+)\.(\d+)(?:\.(\d+))?", t)
    return tuple(int(x or 0) for x in m.groups()) if m else None


def is_major_jump(old: tuple | None, new: tuple | None) -> bool:
    """Крупное ли обновление: растёт первое число (2.1.0 → 3.0.0); у версий 0.x «ломающим» считается рост второго (0.5 → 0.6)."""
    if not old or not new or new <= old:
        return False
    return new[0] > old[0] or (new[0] == 0 and old[0] == 0 and new[1] > old[1])


def _mark_major(updates: list, mods_dir: Path, game_version: str) -> None:
    from .modcheck import read_mod
    for u in updates:
        path = mods_dir / u.file
        try:
            old_text = read_mod(path).version
        except Exception:  # noqa: BLE001
            old_text = ""
        old = core_version(old_text, game_version) or core_version(u.file.removesuffix(".disabled"), game_version)
        new = core_version(u.version, game_version) or core_version(u.new_file, game_version)
        u.old_version = ".".join(map(str, old)) if old else ""
        u.major = is_major_jump(old, new)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _mod_files(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return [f for f in mods_dir.iterdir() if f.is_file() and (f.name.endswith(".jar") or f.name.endswith(".jar.disabled"))]


def _title(path: Path, indexed: str | None) -> str:
    """Понятное название мода: из самого .jar (fabric.mod.json / mods.toml), иначе из списка лаунчера, иначе имя файла.
    Раньше брали название версии с Modrinth — получалось «1.11.3-1.20.1 - Fabric» вместо «Entity Culling»;
    такие названия уже лежат в списке лаунчера, поэтому он только на втором месте."""
    try:
        from .modcheck import read_mod
        name = (read_mod(path).name or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    if name and "${" not in name and name.lower() != "unknown":
        return name
    if indexed:
        return indexed
    plain = path.name.removesuffix(".disabled")
    return plain[:-4] if plain.endswith(".jar") else plain


_KEYS_CACHE: dict = {}


def _keys_sig(game_dir: Path, cf_key: str, mods: list[Path]) -> tuple:
    return (str(game_dir), bool(cf_key), tuple(sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in mods)))


def cached_keys(game_dir: Path, cf_key: str = "") -> dict | None:
    """Готовый результат installed_keys, если папка модов не менялась. Без хэширования — быстро. None — ещё не считали."""
    mods = _mod_files(game_dir / "mods")
    if not mods:
        return {"modrinth": [], "curseforge": []}
    return _valid_cache(game_dir, cf_key, mods)


def _valid_cache(game_dir: Path, cf_key: str, mods: list[Path]) -> dict | None:
    """Результат из кэша. Ошибку сети держим 5 минут: иначе каталог перечитывался бы без конца."""
    cached = _KEYS_CACHE.get(str(game_dir))
    if not cached or cached[0] != _keys_sig(game_dir, cf_key, mods):
        return None
    failed_at = cached[2]
    if failed_at is not None and time.time() - failed_at > 300:
        return None
    return cached[1]


def keys_ok(game_dir: Path) -> bool:
    """Последняя проверка прошла без ошибок сети (тогда можно обновлять каталог)."""
    cached = _KEYS_CACHE.get(str(game_dir))
    return bool(cached and cached[2] is None)


def installed_keys(game_dir: Path, cf_key: str = "", log=print) -> dict:
    """Какие моды из каталога уже стоят в сборке, независимо от того, с какого сайта они ставились.
    Моды на Modrinth и CurseForge имеют разные номера, поэтому сверяем файлы: SHA-1 (Modrinth) и отпечаток (CurseForge).
    Возвращает {"modrinth": {id проекта}, "curseforge": {id мода}}. Результат кэшируется, пока папка не меняется."""
    mods = _mod_files(game_dir / "mods")
    out = {"modrinth": set(), "curseforge": set(), "enabled_modrinth": set(), "enabled_curseforge": set()}
    if not mods:
        return {k: sorted(v) for k, v in out.items()}
    sig = _keys_sig(game_dir, cf_key, mods)
    ready = _valid_cache(game_dir, cf_key, mods)
    if ready is not None:
        return ready
    by_sha = {_sha1(f): f for f in mods}
    failed = False
    try:
        res = post_json(f"{MODRINTH_API}/version_files", {"hashes": list(by_sha), "algorithm": "sha1"}) or {}
        out["modrinth"] = {str(v.get("project_id")) for v in res.values() if isinstance(v, dict) and v.get("project_id")}
        for sha, v in res.items():   # включён ли мод: выключенные лежат как .jar.disabled
            if isinstance(v, dict) and v.get("project_id") and sha in by_sha and not by_sha[sha].name.endswith(".disabled"):
                out["enabled_modrinth"].add(str(v["project_id"]))
    except Exception as exc:  # noqa: BLE001
        failed = True
        log(f"[Каталог] Modrinth не ответил на проверку файлов: {exc}")
    if cf_key:
        from .packformats import cf_fingerprint
        try:
            prints = [cf_fingerprint(f.read_bytes()) for f in mods]
            data = post_json(f"{CURSEFORGE_API}/fingerprints/432", {"fingerprints": prints},
                             headers={"x-api-key": cf_key}).get("data", {})
            by_print = {p: f for p, f in zip(prints, mods)}
            out["curseforge"] = {str((m.get("file") or {}).get("modId")) for m in data.get("exactMatches", [])
                                 if (m.get("file") or {}).get("modId")}
            for m in data.get("exactMatches", []):
                f = m.get("file") or {}
                path = by_print.get(f.get("fileFingerprint"))
                if f.get("modId") and path is not None and not path.name.endswith(".disabled"):
                    out["enabled_curseforge"].add(str(f["modId"]))
        except Exception as exc:  # noqa: BLE001
            failed = True
            log(f"[Каталог] CurseForge не ответил на проверку файлов: {exc}")
    result = {k: sorted(v) for k, v in out.items()}
    _KEYS_CACHE[str(game_dir)] = (sig, result, time.time() if failed else None)
    return result


def check(game_dir: Path, game_version: str, loader: str, cf_key: str = "", log=print) -> list[dict]:
    mods_dir = game_dir / "mods"
    files = _mod_files(mods_dir)
    if not files:
        return []
    index = ModIndex(game_dir)
    titles = {e.get("file"): e.get("title") for e in index.data.values() if isinstance(e, dict)}
    by_sha = {_sha1(f): f for f in files}
    updates: list[ModUpdate] = []
    known: set[Path] = set()

    # --- Modrinth
    try:
        result = post_json(f"{MODRINTH_API}/version_files/update",
                           {"hashes": list(by_sha), "algorithm": "sha1", "loaders": _compatible_loaders(loader, game_version),
                            "game_versions": [game_version]})
        for sha, version in (result or {}).items():
            path = by_sha.get(sha)
            if not path:
                continue
            known.add(path)
            remote = next((x for x in version.get("files", []) if x.get("primary")), None) or (version.get("files") or [None])[0]
            if not remote or any(x.get("hashes", {}).get("sha1") == sha for x in version.get("files", [])):
                continue                                   # уже последняя (у версии может быть несколько файлов)
            plain = path.name.removesuffix(".disabled")
            updates.append(ModUpdate(path.name, _title(path, titles.get(plain)), "modrinth",
                                     version.get("project_id", ""), remote["filename"], remote["url"],
                                     remote.get("hashes", {}).get("sha1", ""), int(remote.get("size") or 0),
                                     version.get("version_number", ""), version_id=str(version.get("id") or "")))
    except Exception as exc:  # noqa: BLE001
        log(f"[Обновления] Modrinth недоступен: {exc}")

    # --- CurseForge (только то, чего нет на Modrinth)
    rest = [f for f in files if f not in known]
    if cf_key and rest:
        from .packformats import cf_fingerprint
        try:
            prints = {cf_fingerprint(f.read_bytes()): f for f in rest}
            data = post_json(f"{CURSEFORGE_API}/fingerprints/432", {"fingerprints": list(prints)},
                             headers={"x-api-key": cf_key}).get("data", {})
            names = {CF_LOADER_NAMES.get(x, "") for x in _compatible_loaders(loader, game_version)} - {""}
            for match in data.get("exactMatches", []):
                current = match.get("file", {})
                path = prints.get(current.get("fileFingerprint"))
                if not path:
                    continue
                fits = [f for f in match.get("latestFiles", [])
                        if game_version in f.get("gameVersions", [])
                        and (not names or names & set(f.get("gameVersions", [])))
                        and f.get("downloadUrl") and f.get("releaseType", 1) == 1]
                if not fits:
                    continue
                best = max(fits, key=lambda f: f.get("fileDate", ""))
                if best.get("id") == current.get("id") or best.get("fileDate", "") <= current.get("fileDate", ""):
                    continue
                plain = path.name.removesuffix(".disabled")
                sha1 = next((h["value"] for h in best.get("hashes", []) if h.get("algo") == CF_HASH_SHA1), "")
                updates.append(ModUpdate(path.name, _title(path, titles.get(plain)), "curseforge",
                                         str(match.get("id") or best.get("modId", "")), best["fileName"],
                                         best["downloadUrl"], sha1, int(best.get("fileLength") or 0),
                                         best.get("displayName", ""), version_id=str(best.get("id") or "")))
        except Exception as exc:  # noqa: BLE001
            log(f"[Обновления] CurseForge недоступен: {exc}")
    skipped = skipped_updates(game_dir)
    updates = [u for u in updates if not (u.version and skipped.get(f"{u.source}:{u.project_id}") == u.version)]
    updates.sort(key=lambda u: u.title.lower())
    try:
        _mark_major(updates, mods_dir, game_version)
    except Exception as exc:  # noqa: BLE001 — пометка не должна ломать проверку обновлений
        log(f"[Обновления] не удалось определить крупные обновления: {exc}")
    return [asdict(u) for u in updates]


_TAG = re.compile(r"<[^>]+>")
_ID = re.compile(r"^[A-Za-z0-9]{1,40}$")
CHANGELOG_MAX = 6000


def _plain(text: str) -> str:
    """HTML/Markdown списка изменений → простой текст для окна лаунчера."""
    import html
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", text or "")
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = html.unescape(_TAG.sub("", text))
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)              # картинки Markdown
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # ссылки Markdown → текст
    text = re.sub(r"(?m)^\s*[-*]\s+", "• ", text)
    text = re.sub(r"(?m)^#+\s*", "", text).replace("**", "").replace("__", "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:CHANGELOG_MAX] + ("…" if len(text) > CHANGELOG_MAX else "")


def changelog(source: str, project_id: str, version_id: str, cf_key: str = "") -> str:
    """Что нового в версии мода. Пусто — автор ничего не написал."""
    from .net import get_json
    if not _ID.match(str(version_id or "")):
        raise ValueError("нет номера версии")
    if source == "modrinth":
        return _plain(get_json(f"{MODRINTH_API}/version/{version_id}", timeout=12, attempts=2).get("changelog") or "")
    if source == "curseforge" and cf_key and _ID.match(str(project_id or "")):
        data = get_json(f"{CURSEFORGE_API}/mods/{project_id}/files/{version_id}/changelog",
                        headers={"x-api-key": cf_key}, timeout=12, attempts=2)
        return _plain(data.get("data") or "")
    raise ValueError("источник не поддерживается")


SKIP_FILE = ".vaultmc_skipped_updates.json"   # версии, от которых игрок откатился: их больше не предлагаем


def _skip_path(game_dir: Path) -> Path:
    return game_dir / SKIP_FILE


def skipped_updates(game_dir: Path) -> dict:
    """{«источник:проект»: номер версии, которую не предлагать}."""
    try:
        data = json.loads(_skip_path(game_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _set_skipped(game_dir: Path, key: str, version: str | None) -> None:
    data = skipped_updates(game_dir)
    if version:
        data[key] = version
    else:
        data.pop(key, None)
    try:
        _skip_path(game_dir).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


BACKUP_DIR = ".vaultmc_modbackup"   # рядом с mods: старые версии модов после обновления (для отката)
BACKUP_KEEP = 40                    # сколько старых файлов хранить всего


def _backup_dir(game_dir: Path) -> Path:
    return game_dir / BACKUP_DIR


def _read_manifest(game_dir: Path) -> list[dict]:
    try:
        data = json.loads((_backup_dir(game_dir) / "manifest.json").read_text(encoding="utf-8"))
        return [e for e in data if isinstance(e, dict) and e.get("backup")]
    except (OSError, ValueError):
        return []


def _write_manifest(game_dir: Path, entries: list[dict]) -> None:
    # лишнее (старше BACKUP_KEEP) удаляем вместе с файлами
    for e in entries[:-BACKUP_KEEP] if len(entries) > BACKUP_KEEP else []:
        (_backup_dir(game_dir) / Path(e["backup"]).name).unlink(missing_ok=True)
    entries = entries[-BACKUP_KEEP:]
    folder = _backup_dir(game_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def list_backups(game_dir: Path) -> list[dict]:
    """Старые версии модов, к которым можно вернуться (новые сверху). Записи без файла отбрасываются."""
    folder = _backup_dir(game_dir)
    res = [e for e in _read_manifest(game_dir) if (folder / Path(e["backup"]).name).is_file()]
    return list(reversed(res))


def apply(game_dir: Path, updates: list[dict], log=print) -> tuple[int, list[str]]:
    """Скачивает новые версии; старые убирает в папку отката. Возвращает (обновлено, ошибки)."""
    import time
    mods_dir = game_dir / "mods"
    index = ModIndex(game_dir)
    done, errors = 0, []
    entries = _read_manifest(game_dir)
    for u in updates:
        old = mods_dir / Path(u["file"]).name
        disabled = old.name.endswith(".disabled")
        new = mods_dir / (Path(u["new_file"]).name + (".disabled" if disabled else ""))
        tmp = mods_dir / (Path(u["new_file"]).name + ".vaultnew")
        try:
            download(u["url"], tmp, u.get("sha1") or None, u.get("size") or None)
            stamp = int(time.time() * 1000)
            keep = f"{stamp}_{old.name}"
            moved = old.exists()
            if moved:
                _backup_dir(game_dir).mkdir(parents=True, exist_ok=True)
                old.replace(_backup_dir(game_dir) / keep)
            try:
                tmp.replace(new)
            except OSError:
                # новый файл не встал на место — возвращаем старый, иначе мод пропал бы из папки
                if moved:
                    (_backup_dir(game_dir) / keep).replace(old)
                raise
            if moved:
                entries.append({"id": str(stamp), "title": u["title"], "old_file": old.name, "new_file": new.name,
                                "backup": keep, "version": u.get("version", ""), "at": stamp // 1000,
                                "source": u["source"], "project_id": u["project_id"]})
            index.data[f"{u['source']}:{u['project_id']}"] = {"title": u["title"], "file": Path(u["new_file"]).name,
                                                              "dir": "mods"}
            done += 1
            _set_skipped(game_dir, f"{u['source']}:{u['project_id']}", None)   # игрок обновил сам — пропуск этой версии снят
            log(f"[Обновления] {u['title']}: {old.name} → {new.name}")
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            errors.append(f"{u['title']}: {exc}")
            log(f"[Обновления] {u['title']}: не удалось — {exc}")
    if done:
        index.path.write_text(json.dumps(index.data, indent=2, ensure_ascii=False), encoding="utf-8")
        _write_manifest(game_dir, entries)
    return done, errors


def rollback(game_dir: Path, backup_id: str) -> str:
    """Возвращает старую версию мода: новый файл убирается, старый ставится на место. Возвращает название мода."""
    entries = _read_manifest(game_dir)
    entry = next((e for e in entries if e.get("id") == backup_id), None)
    if not entry:
        raise ValueError("Эта версия мода уже удалена из архива отката")
    src = _backup_dir(game_dir) / Path(entry["backup"]).name
    if not src.is_file():
        raise ValueError("Файл старой версии пропал — откатить нельзя")
    mods_dir = game_dir / "mods"
    mods_dir.mkdir(exist_ok=True)
    target = mods_dir / Path(entry["old_file"]).name
    current = mods_dir / Path(entry["new_file"]).name
    # если пользователь успел включить/выключить мод — ищем новый файл в обоих видах
    for cand in (current, mods_dir / current.name.removesuffix(".disabled"), mods_dir / (current.name.removesuffix(".disabled") + ".disabled")):
        if cand.exists() and cand != target:
            cand.unlink()
            break
    src.replace(target)
    if entry.get("version"):     # от этой версии откатились — не предлагаем её снова (новее — предложим)
        _set_skipped(game_dir, f"{entry['source']}:{entry['project_id']}", str(entry["version"]))
    index = ModIndex(game_dir)
    index.data[f"{entry['source']}:{entry['project_id']}"] = {"title": entry["title"], "file": target.name.removesuffix(".disabled"),
                                                              "dir": "mods"}
    index.path.write_text(json.dumps(index.data, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_manifest(game_dir, [e for e in entries if e.get("id") != backup_id])
    return entry["title"]
