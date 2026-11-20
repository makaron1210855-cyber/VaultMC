"""Модели существ и предметов из ресурспаков (Entity Model Features): включить или отключить по одной.

Ресурспаки в формате OptiFine кладут свои модели в assets/<пространство>/optifine/cem/<имя>.jem (или emf/cem/…).
Мод Entity Model Features (EMF) их читает. Иногда модель из пака рисуется неправильно: у колокола висит только нижняя
часть, у сундука не хватает крышки и т. п. Исправляется отключением этой одной модели: EMF тогда рисует обычную
(ванильную) модель, а текстуры пака остаются. Список отключённых моделей EMF хранит в config/entity_model_features.json,
в поле modelsNamesDisabled — его и правим (с копией файла рядом). Менять можно, пока игра закрыта: EMF читает настройки при запуске.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import packs

CONFIG = "entity_model_features.json"
BACKUP = CONFIG + ".bak-vaultmc"
_MODEL = re.compile(r"^assets/[^/]+/(?:optifine|emf)/cem/([^/]+)\.jem$", re.I)


def emf_installed(game_dir: Path) -> bool:
    mods = game_dir / "mods"
    return mods.is_dir() and any(f.suffix.lower() == ".jar" and "entity_model_features" in f.name.lower().replace("-", "_")
                                 for f in mods.iterdir() if f.is_file())


def _pack_models(path: Path) -> set[str]:
    """Имена моделей (.jem) в одном ресурспаке: архиве или папке."""
    names: set[str] = set()
    try:
        if path.is_dir():
            for f in path.glob("assets/*/*/cem/*.jem"):
                if f.parent.parent.name.lower() in ("optifine", "emf"):
                    names.add(f.stem.lower())
        elif path.is_file():
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    m = _MODEL.match(n)
                    if m:
                        names.add(m.group(1).lower())
    except (OSError, zipfile.BadZipFile):
        pass
    return names


def _config_path(game_dir: Path) -> Path:
    return game_dir / "config" / CONFIG


def _read_config(game_dir: Path) -> dict:
    try:
        data = json.loads(_config_path(game_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def disabled_names(game_dir: Path) -> list[str]:
    raw = _read_config(game_dir).get("modelsNamesDisabled")
    return [str(x).lower() for x in raw] if isinstance(raw, list) else []


def list_models(game_dir: Path) -> dict:
    """Модели из включённых ресурспаков. {installed: EMF есть?, models: [{name, packs, disabled}]}.
    Сначала отключённые, потом по имени. Отключённая модель показывается, даже если её пак уже выключили."""
    off = set(disabled_names(game_dir))
    folder = game_dir / "resourcepacks"
    by_name: dict[str, list[str]] = {}
    for pack in packs.enabled_packs(game_dir):
        path = folder / pack
        for name in _pack_models(path):
            by_name.setdefault(name, []).append(pack)
    for name in off:
        by_name.setdefault(name, [])
    rows = [{"name": n, "packs": p, "disabled": n in off} for n, p in by_name.items()]
    rows.sort(key=lambda r: (not r["disabled"], r["name"]))
    return {"installed": emf_installed(game_dir), "models": rows}


def set_disabled(game_dir: Path, disable: list[str], enable: list[str]) -> dict:
    """Добавляет/убирает имена в modelsNamesDisabled. Остальные настройки EMF не трогаются; перед первой правкой —
    копия файла (entity_model_features.json.bak-vaultmc)."""
    path = _config_path(game_dir)
    data = _read_config(game_dir)
    current = [str(x) for x in (data.get("modelsNamesDisabled") or [])] if isinstance(data.get("modelsNamesDisabled"), list) else []
    want_off = {str(n).strip().lower() for n in disable if str(n).strip()}
    want_on = {str(n).strip().lower() for n in enable if str(n).strip()}
    result = [n for n in current if n.lower() not in want_on]
    result += [n for n in sorted(want_off) if n.lower() not in {x.lower() for x in result}]
    if result == current:
        return {"ok": True, "disabled": result, "changed": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not (path.parent / BACKUP).exists():
        try:
            (path.parent / BACKUP).write_bytes(path.read_bytes())
        except OSError:
            pass
    data["modelsNamesDisabled"] = result
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "disabled": result, "changed": True}
