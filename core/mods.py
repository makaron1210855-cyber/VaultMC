"""Локальные моды в .minecraft/mods. Отключение = переименование в *.jar.disabled."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ModInfo:
    path: Path

    @property
    def enabled(self) -> bool:
        return self.path.suffix == ".jar"

    @property
    def display_name(self) -> str:
        name = self.path.name
        return name[: -len(".disabled")] if name.endswith(".disabled") else name

    @property
    def size_kb(self) -> int:
        try:
            return self.path.stat().st_size // 1024
        except OSError:
            return 0


class ModManager:
    def __init__(self, game_dir: Path):
        self.game_dir = game_dir

    @property
    def mods_dir(self) -> Path:
        return self.game_dir / "mods"

    def list_mods(self, query: str = "") -> list[ModInfo]:
        if not self.mods_dir.is_dir():
            return []
        query = query.strip().lower()
        files = [p for p in self.mods_dir.iterdir()
                 if p.is_file() and (p.name.endswith(".jar") or p.name.endswith(".jar.disabled"))
                 and query in p.name.lower()]
        return [ModInfo(p) for p in sorted(files, key=lambda p: p.name.lower())]

    def toggle(self, mod: ModInfo) -> ModInfo:
        new_path = mod.path.with_name(mod.path.name + ".disabled") if mod.enabled else mod.path.with_name(mod.display_name)
        mod.path.rename(new_path)
        return ModInfo(new_path)

    def install(self, source: Path) -> Path:
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / source.name
        shutil.copy2(source, target)
        return target

    def delete(self, mod: ModInfo) -> None:
        mod.path.unlink(missing_ok=True)
