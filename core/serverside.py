"""Какие моды сборки нужны выделенному серверу, а какие работают только у игрока.

Сервер с модом «только для клиента» (Sodium, Iris, мини-карта, меню модов) чаще всего не запускается: мод обращается
к классам окна и графики, которых на сервере нет. Поэтому при сборке сервера такие моды не копируем.

Как определяем (по порядку, первое уверенное решение побеждает):
  1. описание внутри .jar: fabric.mod.json "environment": "client", quilt.mod.json minecraft.environment,
     mods.toml clientSideOnly = true (Forge 1.20.6+ / NeoForge) — решение автора мода;
  2. Modrinth по SHA-1 файла: server_side = "unsupported" — так автор отметил проект на Modrinth;
  3. встроенный список известных клиентских модов (по id мода) — для модов не с Modrinth;
  4. иначе мод считаем общим и кладём на сервер.
Мод, от которого обязательно зависит оставленный мод, возвращаем на сервер (иначе сервер не запустится).
Игрок может поправить любой пункт галочкой — это только предложение.
"""
from __future__ import annotations

import json
import tomllib
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .modcheck import read_mod

MODRINTH_API = "https://api.modrinth.com/v2"

# Моды, которые на сервере не нужны или мешают. Только уверенные случаи. Моды со своими звуками и частицами
# (Auditory, Falling Leaves) сюда не входят: они кладут записи в реестры, и сервер без них не пустит игрока с ними.
CLIENT_IDS = {
    "sodium", "sodium-extra", "sodiumextra", "reeses-sodium-options", "reeses_sodium_options", "indium",
    "iris", "oculus", "embeddium", "rubidium", "magnesium", "embeddiumplus", "optifine", "optifabric",
    "modmenu", "continuity", "entityculling", "lambdynlights", "dynamiclights", "betterf3", "zoomify",
    "ok_zoomer", "okzoomer", "logical_zoom", "cullleaves", "enhancedblockentities", "immediatelyfast",
    "dynamic_fps", "dynamicfps", "fpsreducer", "notenoughanimations", "skinlayers3d", "3dskinlayers",
    "firstperson", "firstpersonmod", "mousetweaks", "controlling", "chat_heads", "chatheads", "citresewn",
    "cit_resewn", "legendarytooltips", "fancymenu", "drippyloadingscreen", "yosbr", "betterthirdperson",
    "shulkerboxtooltip_client", "entitytexturefeatures", "entity_texture_features", "entity_model_features",
    "xaerominimap", "xaerominimapfair", "xaeroworldmap", "journeymap_client", "voxelmap", "mapfrontiers_client",
    "betterpingdisplay", "smoothscroll", "smooth_scrolling", "blur", "lambdabettergrass", "fabricskyboxes",
    "puzzle", "nvidium", "bobby", "colormatic", "resourcepackoverrides",
    # туннели и LAN-моды лаунчера: у выделенного сервера свой порт, они ему не нужны
    "e4mc", "e4mc_minecraft", "mcwifipnp",
}
# id из CLIENT_IDS, которые бывают и серверными модами под тем же именем: доверяем им только при подтверждении
# из .jar или Modrinth
_DOUBTFUL = {"xaerominimap", "xaeroworldmap"}

REASONS = {
    "jar": "автор мода отметил его «только для игрока»",
    "modrinth": "на Modrinth отмечен как мод только для игрока",
    "list": "известный мод только для игрока (графика, интерфейс, карта)",
    "tunnel": "мод для открытия мира друзьям — серверу он не нужен",
    "dep": "нужен другому моду на сервере",
    "server": "мод только для сервера",
    "both": "",
    "disabled": "выключен в сборке",
}


@dataclass
class ModSide:
    file: str
    name: str
    side: str                # client | server | both
    reason: str              # ключ REASONS
    ids: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    sha1: str = ""

    @property
    def on_server(self) -> bool:
        return self.side != "client"


def _jar_env(path: Path) -> str:
    """client / server / '' — как сам мод говорит о себе."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for meta in ("fabric.mod.json", "quilt.mod.json"):
                if meta not in names:
                    continue
                data = json.loads(z.read(meta).decode("utf-8", "replace"), strict=False)
                env = data.get("environment") if meta == "fabric.mod.json" else (data.get("minecraft") or {}).get("environment")
                env = str(env or "").strip().lower()
                if env in ("client", "server"):
                    return env
                if env:
                    return ""
            for toml in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if toml not in names:
                    continue
                try:
                    data = tomllib.loads(z.read(toml).decode("utf-8", "replace"))
                except tomllib.TOMLDecodeError:
                    return ""
                if data.get("clientSideOnly") is True:
                    return "client"
                return ""
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        pass
    return ""


def modrinth_sides(sha1s: list[str], timeout: int = 15) -> dict[str, str]:
    """sha1 → client_side/server_side с Modrinth: {sha1: 'client'|'server'|'both'}. Нет связи — пустой словарь."""
    from .net import get_json, post_json
    if not sha1s:
        return {}
    out: dict[str, str] = {}
    try:
        versions = post_json(f"{MODRINTH_API}/version_files", {"hashes": sha1s, "algorithm": "sha1"}, timeout=timeout)
        by_project: dict[str, list[str]] = {}
        for h, v in (versions or {}).items():
            if isinstance(v, dict) and v.get("project_id"):
                by_project.setdefault(str(v["project_id"]), []).append(h)
        ids = list(by_project)
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            projects = get_json(f"{MODRINTH_API}/projects?ids={json.dumps(chunk, separators=(',', ':'))}", timeout=timeout)
            for p in projects or []:
                server, client = str(p.get("server_side", "")), str(p.get("client_side", ""))
                side = "client" if server == "unsupported" else ("server" if client == "unsupported" else "both")
                for h in by_project.get(str(p.get("id", "")), []):
                    out[h] = side
    except Exception:  # noqa: BLE001 — без Modrinth решаем по .jar и списку
        return out
    return out


def _sha1(path: Path) -> str:
    from .net import sha1_file
    try:
        return sha1_file(path)
    except OSError:
        return ""


def classify(mods_dir: Path, online: bool = True) -> list[ModSide]:
    """Все включённые моды сборки с решением «на сервер или нет». Выключенные (.disabled) не берём вовсе."""
    files = sorted((f for f in mods_dir.glob("*.jar") if f.is_file()), key=lambda f: f.name.lower()) if mods_dir.is_dir() else []
    items: list[ModSide] = []
    for f in files:
        info = read_mod(f)
        name = (info.name or "").strip()
        if not name or "${" in name:
            name = f.stem
        ids = [i.lower() for i in info.ids if i]
        items.append(ModSide(f.name, name, "both", "both", ids, [d.lower() for d in info.depends], _sha1(f)))
    remote = modrinth_sides([m.sha1 for m in items if m.sha1]) if online else {}
    for m, f in zip(items, files):
        decide(m, _jar_env(f), remote.get(m.sha1, ""))
    keep_dependencies(items)
    return items


def decide(m: ModSide, jar_env: str, modrinth: str) -> None:
    """Решение по одному моду (отдельно — чтобы проверять тестами без файлов и сети)."""
    tunnel = any(i in ("e4mc", "e4mc_minecraft", "mcwifipnp") for i in m.ids)
    listed = any(i in CLIENT_IDS for i in m.ids) or "optifine" in m.file.lower()
    listed_ids = [i for i in m.ids if i in CLIENT_IDS]
    doubtful = bool(listed_ids) and all(i in _DOUBTFUL for i in listed_ids)
    if tunnel:
        m.side, m.reason = "client", "tunnel"
    elif jar_env == "client":
        m.side, m.reason = "client", "jar"
    elif jar_env == "server":
        m.side, m.reason = "server", "server"
    elif modrinth == "client":
        m.side, m.reason = "client", "modrinth"
    elif modrinth == "server":
        m.side, m.reason = "server", "server"
    elif listed and not doubtful and modrinth != "both":
        m.side, m.reason = "client", "list"
    else:
        m.side, m.reason = "both", "both"


def keep_dependencies(items: list[ModSide]) -> None:
    """Если мод на сервере обязательно требует мод, который мы убрали, — возвращаем зависимость.
    Исключение — моды, которые сам автор отметил клиентскими: загрузчик на сервере их всё равно не загрузит."""
    provides: dict[str, ModSide] = {}
    for m in items:
        for i in m.ids:
            provides.setdefault(i, m)
    changed = True
    while changed:
        changed = False
        for m in items:
            if not m.on_server:
                continue
            for dep in m.depends:
                target = provides.get(dep)
                if target and not target.on_server and target.reason not in ("jar", "tunnel"):
                    target.side, target.reason = "both", "dep"
                    changed = True


def to_dicts(items: list[ModSide]) -> list[dict]:
    return [{**asdict(m), "on_server": m.on_server, "why": REASONS.get(m.reason, "")} for m in items]
