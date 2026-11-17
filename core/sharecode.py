"""
«Поделиться сборкой» кодом из 6 символов.

Внутри кода — то же, что в .mrpack: версия игры, загрузчик и ссылки на точные файлы модов на Modrinth
(со сверкой SHA-1/SHA-512), плюс небольшие файлы (настройки модов, options.txt) — если влезают.
Моды, которых нет на Modrinth (скачанные вручную или только с CurseForge), в код не помещаются —
их список показываем, чтобы друг поставил их сам.
"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .instances import Instance, InstanceManager
from .packformats import CONTENT_DIRS, OVERRIDE_DIRS, OVERRIDE_FILES, export_mrpack, import_any

MAX_JSON = 500 * 1024        # сервер принимает до 512 КБ на код


def build_pack(inst: Instance, log=print) -> tuple[dict, list[str]]:
    """Собирает описание сборки для кода. Возвращает (pack, моды-которые-не-влезли)."""
    with tempfile.TemporaryDirectory() as tmp:
        mr = export_mrpack(inst, Path(tmp) / "pack", log)
        with zipfile.ZipFile(mr) as zf:
            index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
            overrides = [n for n in zf.namelist() if n.startswith("overrides/") and not n.endswith("/")]
            jars = [n for n in overrides if n.split("/")[1] in CONTENT_DIRS]   # моды/ресурспаки не с Modrinth
            small = [n for n in overrides if n not in jars]
            skipped = [Path(n).name for n in jars]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
                for n in small:
                    out.writestr(n, zf.read(n))
            full = buf.getvalue()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
                if "overrides/options.txt" in small:
                    out.writestr("overrides/options.txt", zf.read("overrides/options.txt"))
            only_opts = buf.getvalue()
    pack = None
    for blob in (full, only_opts, b""):
        pack = {"format": 1, "name": inst.name, "loader": inst.loader, "game_version": inst.game_version,
                "index": index, "overrides": base64.b64encode(blob).decode() if blob else "", "manual": skipped}
        if len(json.dumps(pack, ensure_ascii=False).encode()) <= MAX_JSON:
            if blob is not full:
                log("[Код сборки] Настройки модов слишком большие для кода — передаю без них")
            break
    else:
        raise ValueError("Сборка слишком большая для кода — используйте экспорт в файл .mrpack")
    return pack, skipped


# Откуда сборка по коду может качать файлы — те же сайты, что разрешает формат Modrinth (.mrpack).
ALLOWED_HOSTS = ("cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com")


def _allowed_url(url) -> bool:
    host = (urlsplit(str(url)).hostname or "").lower()
    return str(url).startswith("https://") and host in ALLOWED_HOSTS


def install_pack(pack: dict, manager: InstanceManager, api_key: str = "", log=print) -> tuple[Instance, list[str]]:
    """Собирает сборку из кода: делаем из него .mrpack и ставим обычным импортом.
    Код может прислать кто угодно, поэтому моды берём только по ссылкам Modrinth/GitHub/GitLab,
    а готовые .jar внутри кода не принимаем (лаунчер сам такие в код не кладёт)."""
    if not isinstance(pack, dict) or not isinstance(pack.get("index"), dict):
        raise ValueError("Код сборки повреждён")
    index = dict(pack["index"])
    files, dropped = [], []
    for f in index.get("files") or []:
        if not isinstance(f, dict):
            continue
        urls = [u for u in f.get("downloads") or [] if _allowed_url(u)]
        if urls:
            files.append({**f, "downloads": urls})
        else:
            dropped.append(Path(str(f.get("path", "?"))).name)
    index["files"] = files
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{_safe(pack.get('name') or 'Сборка')}.mrpack"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("modrinth.index.json", json.dumps(index, ensure_ascii=False))
            if pack.get("overrides"):
                with zipfile.ZipFile(io.BytesIO(base64.b64decode(pack["overrides"]))) as src:
                    for n in src.namelist():
                        if n.endswith("/"):
                            continue
                        if _safe_override(n):
                            zf.writestr(n, src.read(n))
                        else:
                            dropped.append(n.replace("\\", "/").split("/")[-1])   # моды/ресурспаки и всё лишнее — не берём
        inst, problems = import_any(path, manager, api_key, log)
    if dropped:
        log(f"[Код сборки] Пропущены файлы не с Modrinth: {', '.join(dropped[:8])}")
    manual = [str(m) for m in pack.get("manual") or []] + dropped
    if manual:
        problems = list(problems) + [f"Эти моды друг ставил не с Modrinth — добавьте их сами: {', '.join(manual[:8])}"
                                     + (f" и ещё {len(manual) - 8}" if len(manual) > 8 else "")]
    return inst, problems


def _safe_override(name: str) -> bool:
    """Из кода принимаем только то, что лаунчер сам кладёт в код: настройки (config/, kubejs/ и т. п.) и options.txt.
    Раньше «мод файлом» отсекался по имени папки mods/ — но `overrides/./mods/x.jar`, `overrides/Mods/x.jar` или
    имя с обратной косой чертой проходили, и чужой .jar попадал бы в игру. Теперь — белый список и строгий разбор пути."""
    parts = name.split("/")
    if len(parts) < 2 or parts[0] != "overrides":
        return False
    rest = parts[1:]
    for p in rest:
        # пустые части, «.», «..», обратная косая, диск (C:), точка/пробел в конце (Windows их отбрасывает) — не берём
        if not p or p in (".", "..") or "\\" in p or ":" in p or p != p.rstrip(" ."):
            return False
    if len(rest) == 1:
        return rest[0] in OVERRIDE_FILES
    return rest[0] in OVERRIDE_DIRS and not rest[-1].lower().endswith((".jar", ".disabled"))


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_")[:40] or "Сборка"
