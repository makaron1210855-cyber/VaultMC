"""
Автонастройка сборки под компьютер: смотрим память, процессор и видеокарту и подбираем
память для игры, настройки графики (options.txt) и моды оптимизации.

Честно о границах: FPS в самой игре лаунчер не измеряет (для этого нужен мод внутри игры) —
подбор идёт по характеристикам железа, а не по замеру.
"""
from __future__ import annotations

import os
import platform
import sys

from .gameopts import PRESETS
from .utils import total_ram_gb

GPU_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
SKIP_GPU = ("microsoft basic", "remote display", "virtual", "parsec", "meta virtual", "dummy", "idd")


def _winreg_pc() -> tuple[str, list[dict]]:
    import winreg  # type: ignore[import-not-found]
    cpu = ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            cpu = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    gpus: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GPU_CLASS) as root:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = str(winreg.QueryValueEx(k, "DriverDesc")[0]).strip()
                        vram = 0
                        for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                raw = winreg.QueryValueEx(k, value)[0]
                                vram = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
                                if vram:
                                    break
                            except (OSError, ValueError, TypeError):
                                continue
                except OSError:
                    continue
                if name and not any(s in name.lower() for s in SKIP_GPU) and name not in [g["name"] for g in gpus]:
                    gpus.append({"name": name, "vram_gb": round(vram / 1024 ** 3, 1) if vram else 0})
    except OSError:
        pass
    return cpu, gpus


def gpu_kind(name: str) -> str:
    """discrete | integrated | unknown — по названию видеокарты."""
    import re
    n = name.lower()
    if re.search(r"\bmx ?\d{3}", n):
        return "discrete_low"            # GeForce MX — отдельная, но слабая (ноутбуки)
    if re.search(r"arc(\(tm\))? ?[ab]\d{3}", n):
        return "discrete"                # Intel Arc A/B — отдельная
    if any(k in n for k in ("rtx", "gtx", "quadro", "radeon rx", "radeon pro", "rx ", "titan")) or "nvidia" in n:
        return "discrete"
    if any(k in n for k in ("intel", "uhd", "iris", "vega", "radeon(tm) graphics", "radeon graphics")):
        return "integrated"
    return "unknown"


def detect_pc() -> dict:
    cpu, gpus = ("", [])
    if sys.platform == "win32":
        try:
            cpu, gpus = _winreg_pc()
        except Exception:  # noqa: BLE001
            pass
    if not cpu:
        cpu = platform.processor() or ""
    for g in gpus:
        g["kind"] = gpu_kind(g["name"])
    kinds = {g["kind"] for g in gpus}
    return {
        "ram_gb": total_ram_gb() or 0,
        "cores": os.cpu_count() or 0,
        "cpu": cpu,
        "gpus": gpus,
        "has_discrete": "discrete" in kinds,
        "hybrid": bool(kinds & {"discrete", "discrete_low"}) and "integrated" in kinds,   # ноутбук: встроенная + мощная
    }


def plan_for(pc: dict, mods_count: int, loader: str) -> dict:
    """Что поставить: уровень ПК, память, графика, моды. Возвращает и пояснения для интерфейса."""
    ram, cores, discrete = pc.get("ram_gb") or 0, pc.get("cores") or 0, pc.get("has_discrete")
    if ram and ram < 8 or (cores and cores <= 4 and not discrete):
        tier = "weak"
    elif ram >= 16 and cores >= 8 and discrete:
        tier = "strong"
    else:
        tier = "medium"

    want = {"weak": 3, "medium": 4, "strong": 8}[tier]    # у сильных ПК сборки с шейдерами и сотней модов тесны в 6 ГБ
    if mods_count >= 120:
        want += 2
    elif mods_count >= 40:
        want += 1
    if ram:
        want = min(want, max(2, ram - 4 if ram >= 8 else ram // 2))   # системе оставляем запас
    want = max(2, want)

    options = dict(PRESETS["potato" if tier == "weak" else "balanced"])
    if tier == "medium":
        options.update({"renderDistance": "8", "simulationDistance": "6"})
    if tier == "strong":
        options.update({"renderDistance": "12", "simulationDistance": "10", "maxFps": "260"})

    why = []
    why.append(f"Память игры: {want} ГБ" + (f" (у ПК {ram} ГБ, системе остаётся запас)" if ram else ""))
    why.append(f"Прорисовка: {options['renderDistance']} чанков, графика «{'быстрая' if tier == 'weak' else 'детальная'}»")
    if loader in ("fabric", "quilt", "forge", "neoforge"):
        why.append("Моды оптимизации: недостающие из набора (FPS, память, загрузка)")
    if pc.get("hybrid"):
        why.append("Игра будет запускаться на мощной видеокарте, а не на встроенной")
    return {"tier": tier, "tier_label": {"weak": "Слабый", "medium": "Средний", "strong": "Мощный"}[tier],
            "ram_gb": want, "options": options, "mods": loader in ("fabric", "quilt", "forge", "neoforge"),
            "discrete_gpu": bool(pc.get("hybrid")), "why": why}


def apply_options(game_dir, options: dict) -> None:
    path = game_dir / "options.txt"
    lines: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                lines[k] = v
    lines.update(options)
    game_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{k}:{v}" for k, v in lines.items()) + "\n", encoding="utf-8")
