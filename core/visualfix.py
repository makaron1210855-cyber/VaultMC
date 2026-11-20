"""«Картинка выглядит не так»: найти, какой слой ресурспаков/модов портит вид, и выключить его — обратимо.

Ресурспаки в формате OptiFine приносят в игру много «слоёв»: свои модели существ и блоков (мод Entity Model Features),
свои текстуры и светящиеся детали (Entity Texture Features), соединённые текстуры стёкол (Continuity), улучшенные
блоки-сущности (Enhanced Block Entities). Любой из них может нарисовать что-то неправильно: у колокола висит
только низ, у кровати нет ножек, на мобах лишние пятна. Угадать по картинке, кто виноват, нельзя — поэтому здесь
каждый слой можно выключить одной кнопкой и посмотреть в игре. Всё записывается (.vaultmc-visualfix.json):
«Вернуть как было» восстанавливает прежние значения. Менять можно, пока игра закрыта — моды читают настройки при запуске.

  scan()         — что делают включённые паки, какие слои есть в сборке и в каком они состоянии, модели из пака
  set_layer()    — включить/выключить слой (настройка мода в config/)
  set_models()   — отключить/включить отдельные модели (список EMF) — см. core/emfmodels.py
  reset_all()    — вернуть всё, что меняли отсюда
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import emfmodels, packs

STATE = ".vaultmc-visualfix.json"

# Слои: настройка мода (файл в config/, путь к ключу), её «рабочее» значение, подпись и подсказка.
# json — вложенные ключи через точку; properties — строки key=value (как в Java).
LAYERS = (
    {"id": "etf.textures", "mod": "entity_texture_features", "label": "Свои текстуры существ и блоков из пака",
     "hint": "Пятнистые коровы, разные глаза, подмена текстур сундуков и колоколов. Если у мобов или блоков странные пятна или текстуры не те — выключите.",
     "file": "entity_texture_features.json", "kind": "json", "key": "enableCustomTextures", "on": True},
    {"id": "etf.blocks", "mod": "entity_texture_features", "label": "Свои текстуры блоков-сущностей (сундуки, кровати, колокола, таблички)",
     "hint": "Если именно сундук, кровать, колокол или табличка выглядят неправильно.",
     "file": "entity_texture_features.json", "kind": "json", "key": "enableCustomBlockEntities", "on": True},
    {"id": "etf.emissive", "mod": "entity_texture_features", "label": "Светящиеся детали (глаза, руды) на существах",
     "hint": "Если в темноте что-то светится не там или слишком ярко.",
     "file": "entity_texture_features.json", "kind": "json", "key": "enableEmissiveTextures", "on": True},
    {"id": "etf.armor", "mod": "entity_texture_features", "label": "Свои текстуры брони и отделки",
     "hint": "Если броня выглядит странно.",
     "file": "entity_texture_features.json", "kind": "json", "key": "enableArmorAndTrims", "on": True},
    {"id": "continuity.ctm", "mod": "continuity", "label": "Соединённые текстуры (стёкла, книжные полки, камень)",
     "hint": "Если стёкла или блоки склеиваются не так, видны швы и лишние рёбра.",
     "file": "continuity.json", "kind": "json", "key": "connected_textures", "on": True},
    {"id": "continuity.emissive", "mod": "continuity", "label": "Светящиеся блоки из пака",
     "hint": "Если блоки светятся там, где не должны.",
     "file": "continuity.json", "kind": "json", "key": "emissive_textures", "on": True},
    {"id": "continuity.layers", "mod": "continuity", "label": "Особые слои блоков из пака",
     "hint": "Если стёкла или листва рисуются странно (прозрачность, чёрные куски).",
     "file": "continuity.json", "kind": "json", "key": "custom_block_layers", "on": True},
    {"id": "ebe.chests", "mod": "enhancedblockentities", "label": "«Улучшенные» сундуки (Enhanced Block Entities)",
     "hint": "Если у сундуков пропала крышка или они мерцают.",
     "file": "enhanced_bes.properties", "kind": "properties", "key": "render_enhanced_chests", "on": "true"},
    {"id": "ebe.beds", "mod": "enhancedblockentities", "label": "«Улучшенные» кровати (Enhanced Block Entities)",
     "hint": "Если с кроватью что-то не так.",
     "file": "enhanced_bes.properties", "kind": "properties", "key": "render_enhanced_beds", "on": "true"},
    {"id": "ebe.bells", "mod": "enhancedblockentities", "label": "«Улучшенные» колокола (Enhanced Block Entities)",
     "hint": "Если с колоколом что-то не так.",
     "file": "enhanced_bes.properties", "kind": "properties", "key": "render_enhanced_bells", "on": "true"},
    {"id": "ebe.signs", "mod": "enhancedblockentities", "label": "«Улучшенные» таблички (Enhanced Block Entities)",
     "hint": "Если текст на табличках пропал или мерцает.",
     "file": "enhanced_bes.properties", "kind": "properties", "key": "render_enhanced_signs", "on": "true"},
)

# Что умеют паки: признак в путях файлов пака → возможность и мод, который её читает.
FEATURES = (
    {"id": "cem", "label": "свои модели существ и блоков", "mod": "entity_model_features", "mod_name": "Entity Model Features",
     "re": re.compile(r"^assets/[^/]+/(?:optifine|emf)/cem/[^/]+\.(?:jem|jpm)$", re.I)},
    {"id": "random", "label": "случайные и разные текстуры существ", "mod": "entity_texture_features", "mod_name": "Entity Texture Features",
     "re": re.compile(r"^assets/[^/]+/(?:optifine|etf)/(?:random|mob)/", re.I)},
    {"id": "emissive", "label": "светящиеся детали", "mod": "entity_texture_features", "mod_name": "Entity Texture Features",
     "re": re.compile(r"^assets/[^/]+/textures/entity/.+_e\.png$", re.I)},
    {"id": "ctm", "label": "соединённые текстуры", "mod": "continuity", "mod_name": "Continuity",
     "re": re.compile(r"^assets/[^/]+/(?:optifine|continuity)/ctm/", re.I)},
    {"id": "cit", "label": "свои текстуры предметов (CIT)", "mod": "citresewn", "mod_name": "CIT Resewn",
     "re": re.compile(r"^assets/[^/]+/(?:optifine|citresewn)/cit/", re.I)},
    {"id": "pbr", "label": "карты нормалей и бликов (для шейдеров)", "mod": "", "mod_name": "",
     "re": re.compile(r"^assets/minecraft/textures/(?:block|entity)/.+_(?:n|s)\.png$", re.I)},
)


def _mod_installed(game_dir: Path, token: str) -> bool:
    mods = game_dir / "mods"
    if not token or not mods.is_dir():
        return bool(not token)
    norm = lambda s: re.sub(r"[-_ ]", "", s.lower())      # noqa: E731
    tok = norm(token)
    return any(f.suffix.lower() == ".jar" and tok in norm(f.name) for f in mods.iterdir() if f.is_file())


# ------------------------------------------------------------------ состояние (что меняли отсюда)
def _state(game_dir: Path) -> dict:
    try:
        data = json.loads((game_dir / STATE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(game_dir: Path, state: dict) -> None:
    path = game_dir / STATE
    try:
        if not state.get("layers") and not state.get("models"):
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------ чтение/запись настроек
def _json_get(data: dict, key: str):
    cur = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _json_set(data: dict, key: str, value) -> None:
    parts = key.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _read(game_dir: Path, layer: dict):
    path = game_dir / "config" / layer["file"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if layer["kind"] == "json":
        try:
            return _json_get(json.loads(text), layer["key"])
        except ValueError:
            return None
    m = re.search(rf"^{re.escape(layer['key'])}\s*=\s*(.*?)\s*$", text, re.M)
    return m.group(1) if m else None


def _write(game_dir: Path, layer: dict, value) -> None:
    path = game_dir / "config" / layer["file"]
    text = path.read_text(encoding="utf-8")
    if layer["kind"] == "json":
        data = json.loads(text)
        _json_set(data, layer["key"], value)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    line = f"{layer['key']}={value}"
    pat = re.compile(rf"^{re.escape(layer['key'])}\s*=.*$", re.M)
    text = pat.sub(lambda _m: line, text, count=1) if pat.search(text) else text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def _is_on(layer: dict, value) -> bool:
    return str(value).strip().lower() == str(layer["on"]).strip().lower()


def _off_value(layer: dict):
    return (not layer["on"]) if isinstance(layer["on"], bool) else ("false" if str(layer["on"]).lower() == "true" else "true")


# ------------------------------------------------------------------ что делают паки
def _pack_features(path: Path) -> set[str]:
    found: set[str] = set()
    try:
        if path.is_dir():
            names = (str(p.relative_to(path)).replace("\\", "/") for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            with zipfile.ZipFile(path) as z:
                names = iter(z.namelist())
        else:
            return found
        for n in names:
            for feat in FEATURES:
                if feat["id"] not in found and feat["re"].match(n):
                    found.add(feat["id"])
            if len(found) == len(FEATURES):
                break
    except (OSError, zipfile.BadZipFile):
        pass
    return found


def scan(game_dir: Path) -> dict:
    state = _state(game_dir)
    saved = state.get("layers", {})
    folder = game_dir / "resourcepacks"
    pack_rows = []
    for pack in reversed(packs.enabled_packs(game_dir)):                  # самый важный пак первым
        feats = _pack_features(folder / pack)
        if feats:
            pack_rows.append({"name": pack, "features": [
                {"id": f["id"], "label": f["label"], "mod": f["mod_name"], "installed": _mod_installed(game_dir, f["mod"])}
                for f in FEATURES if f["id"] in feats]})
    layers = []
    for layer in LAYERS:
        if not _mod_installed(game_dir, layer["mod"]):
            continue
        value = _read(game_dir, layer)
        if value is None:
            continue
        layers.append({"id": layer["id"], "label": layer["label"], "hint": layer["hint"],
                       "enabled": _is_on(layer, value), "changed": layer["id"] in saved})
    models = emfmodels.list_models(game_dir)
    ours = set(state.get("models", []))
    for row in models["models"]:
        row["changed"] = row["name"] in ours
    return {"packs": pack_rows, "layers": layers, "models": models["models"], "emf": models["installed"],
            "changed": bool(saved or ours)}


def set_layer(game_dir: Path, layer_id: str, enabled: bool) -> dict:
    layer = next((x for x in LAYERS if x["id"] == layer_id), None)
    if layer is None:
        return {"error": "Неизвестный слой"}
    current = _read(game_dir, layer)
    if current is None:
        return {"error": "Настройка не найдена: запустите игру один раз, мод создаст свой файл настроек"}
    state = _state(game_dir)
    saved = state.setdefault("layers", {})
    if layer_id not in saved:
        saved[layer_id] = {"file": layer["file"], "key": layer["key"], "was": current}
    try:
        _write(game_dir, layer, layer["on"] if enabled else _off_value(layer))
    except (OSError, ValueError) as exc:
        return {"error": f"Не удалось изменить настройку: {exc}"}
    if _is_on(layer, saved[layer_id]["was"]) == bool(enabled):             # вернули в исходное — запоминать нечего
        saved.pop(layer_id, None)
    _save_state(game_dir, state)
    return {"ok": True}


def set_models(game_dir: Path, disable: list[str], enable: list[str]) -> dict:
    before = set(emfmodels.disabled_names(game_dir))
    res = emfmodels.set_disabled(game_dir, disable, enable)
    state = _state(game_dir)
    ours = set(state.get("models", []))
    ours |= {n.lower() for n in disable if n.lower() not in before}
    ours -= {n.lower() for n in enable}
    state["models"] = sorted(ours)
    _save_state(game_dir, state)
    return res


def reset_all(game_dir: Path) -> dict:
    state = _state(game_dir)
    restored, errors, left = [], [], {}
    for lid, info in list(state.get("layers", {}).items()):
        layer = next((x for x in LAYERS if x["id"] == lid), None)
        if not layer:
            continue
        try:
            _write(game_dir, layer, info["was"])
            restored.append(layer["label"])
        except (OSError, ValueError) as exc:
            errors.append(f"{layer['label']}: {exc}")
            left[lid] = info                                                  # не вернулось — запись остаётся
    models = state.get("models", [])
    if models:
        emfmodels.set_disabled(game_dir, [], models)
        restored += [f"модель «{m}»" for m in models]
    _save_state(game_dir, {"layers": left} if left else {})
    return {"ok": not errors, "restored": restored, "errors": errors}
