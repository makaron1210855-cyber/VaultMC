"""Пресеты options.txt — настройки графики самой игры для слабых ПК."""

from __future__ import annotations

from pathlib import Path

# Ключи разных версий игры; незнакомые ключи игра пропускает.
PRESETS = {
    "potato": {   # «Максимум FPS»
        "renderDistance": "6", "simulationDistance": "5", "graphicsMode": "0", "fancyGraphics": "false",
        "renderClouds": "\"false\"", "particles": "2", "entityShadows": "false", "ao": "false",
        "biomeBlendRadius": "0", "mipmapLevels": "0", "entityDistanceScaling": "0.5", "maxFps": "260",
        "enableVsync": "false", "useVbo": "true",
    },
    "balanced": {  # «Баланс»
        "renderDistance": "10", "simulationDistance": "8", "graphicsMode": "1", "fancyGraphics": "true",
        "renderClouds": "\"fast\"", "particles": "1", "entityShadows": "true", "ao": "true",
        "biomeBlendRadius": "2", "mipmapLevels": "2", "entityDistanceScaling": "1.0", "maxFps": "144",
        "enableVsync": "false",
    },
}
PRESET_LABELS = {"potato": "Максимум FPS", "balanced": "Баланс"}


def apply_preset(game_dir: Path, preset: str) -> Path:
    path = game_dir / "options.txt"
    lines: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                lines[key] = value
    lines.update(PRESETS[preset])
    game_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{k}:{v}" for k, v in lines.items()) + "\n", encoding="utf-8")
    return path
