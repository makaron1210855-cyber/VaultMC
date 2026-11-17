"""«Почему тормозит»: разбор логов игры после сеанса. FPS внутри игры лаунчер не измеряет — смотрим только то, что
игра и Java сами пишут в журналы:
  • latest.log     — «Can't keep up!» (тик игры не успевает), ошибки-спам, время загрузки;
  • vaultmc-gc.log — паузы сборщика мусора (журнал включает лаунчер на Java 9+).
Находка: {"level": "bad|warn|info", "title", "text", "fix": {"action", "arg", "label"} | None}; действия — как в core/crash.py.
"""
from __future__ import annotations

import collections
import gzip
import re
from pathlib import Path

KEEP_ERR = 2000
MAX_READ = 6_000_000      # читаем не больше 6 МБ журнала: старые «болтливые» сборки бывают на сотни МБ

BEHIND_RE = re.compile(r"Can't keep up! Is the (?:server|game) overloaded\? Running (\d+)ms or (\d+) ticks? behind")
# строки вида «[2026-11-17T13:44:21.482+0300][12.3s] GC(9) Pause Full (G1 Compaction Pause) 500M->300M(512M) 1234.567ms»
GC_PAUSE_RE = re.compile(r"\]\[(?P<up>[\d.,]+)s\].*?\bPause (?P<kind>[^\n]*?) (?P<ms>[\d.,]+)ms\s*$")
STARTED_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \[.*?\]: (?:Loading Minecraft|Backend library:|Setting user:)")
MOD_ERR_RE = re.compile(r"^\[[\d:]+\] \[([^\]/]+)(?:/[^\]]*)?/(?:ERROR|FATAL)\]")
MOD_NAME_RE = re.compile(r"\[([\w .\-]{2,40})\]: ")


def _read_tail(path: Path) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > MAX_READ:
                f.seek(size - MAX_READ)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def read_game_log(game_dir: Path) -> str:
    """Журнал последнего сеанса: logs/latest.log (если его нет — самый свежий .log.gz)."""
    latest = game_dir / "logs" / "latest.log"
    if latest.is_file():
        return _read_tail(latest)
    gz = sorted((game_dir / "logs").glob("*.log.gz"), key=lambda p: p.stat().st_mtime) if (game_dir / "logs").is_dir() else []
    if gz:
        try:
            with gzip.open(gz[-1], "rb") as f:
                return f.read(MAX_READ).decode("utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def gc_stats(text: str) -> dict:
    """Паузы сборщика мусора: сколько, самая длинная, сколько дольше 100 и 500 мс, сколько полных сборок."""
    pauses, full = [], 0
    for line in text.splitlines():
        m = GC_PAUSE_RE.search(line)
        if not m:
            continue
        try:
            ms = float(m["ms"].replace(",", "."))
        except ValueError:
            continue
        pauses.append(ms)
        if "Full" in m["kind"]:
            full += 1
    return {"count": len(pauses), "max_ms": max(pauses, default=0.0), "over100": sum(p > 100 for p in pauses),
            "over500": sum(p > 500 for p in pauses), "full": full}


def analyze(game_log: str, gc_log: str = "", ram_gb: int = 4, java_major: int = 0, mods_count: int = 0) -> dict:
    findings: list[dict] = []
    lines = game_log.splitlines()

    # --- тик не успевает
    behind = [(int(m[1]), int(m[2])) for m in (BEHIND_RE.search(l) for l in lines) if m]
    if behind:
        worst_ms = max(ms for ms, _ in behind)
        level = "bad" if worst_ms >= 5000 or len(behind) >= 20 else "warn"
        findings.append({
            "level": level, "title": f"Игра «не успевала» {len(behind)} раз(а)",
            "text": f"Самая долгая задержка — {worst_ms / 1000:.1f} с ({max(t for _, t in behind)} тиков). Так бывает, когда процессор "
                    "или память не справляются: слишком большая прорисовка, много мобов и механизмов, тяжёлый мод или нехватка памяти.",
            "fix": {"action": "optimize", "arg": "", "label": "Открыть «Оптимизацию» сборки"}})

    # --- паузы сборщика мусора
    gc = gc_stats(gc_log)
    if gc["count"] and (gc["over500"] or gc["full"] >= 3 or gc["over100"] >= 5):
        bits = []
        if gc["over500"]:
            bits.append(f"{gc['over500']} пауз длиннее полсекунды")
        if gc["over100"] and not gc["over500"]:
            bits.append(f"{gc['over100']} пауз длиннее 0,1 с")
        if gc["full"]:
            bits.append(f"{gc['full']} полных сборок мусора")
        findings.append({
            "level": "bad" if gc["over500"] or gc["full"] >= 5 else "warn", "title": "Игра подвисает из-за уборки памяти",
            "text": f"Java убирала память с паузами: {', '.join(bits)}; самая длинная — {gc['max_ms'] / 1000:.2f} с. "
                    f"Сейчас игре выделено {ram_gb} ГБ — при такой нагрузке этого мало. Добавьте памяти.",
            "fix": {"action": "ram", "arg": ram_gb + 2, "label": f"Выделить {ram_gb + 2} ГБ"}})
    elif gc["count"] and ram_gb >= 10 and gc["max_ms"] < 50 and mods_count < 40:
        findings.append({"level": "info", "title": "Памяти больше, чем нужно",
                         "text": f"Паузы уборки памяти короткие (до {gc['max_ms']:.0f} мс), а игре выделено {ram_gb} ГБ. Лишняя память не ускоряет "
                                 "игру; можно оставить 6–8 ГБ, а остальное — системе.", "fix": None})

    # --- ошибки-спам от одного мода
    errs = collections.Counter()
    for l in lines:
        m = MOD_ERR_RE.match(l)
        if m:
            errs[m.group(1)] += 1
    noisy = [(n, c) for n, c in errs.most_common(3) if c >= 200 and n not in ("main", "Render thread", "Server thread")]
    for name, count in noisy[:2]:
        findings.append({"level": "warn", "title": f"«{name}» засыпает журнал ошибками ({count})",
                         "text": "Постоянные ошибки в журнале сами тормозят игру (запись на диск, обработка исключений). Обычно это значит, "
                                 "что мод работает неправильно: обновите его или выключите.", "fix": {"action": "logs", "arg": "", "label": "Открыть консоль"}})
    render_errs = errs.get("Render thread", 0) + errs.get("main", 0)
    if render_errs >= 200:
        findings.append({"level": "warn", "title": f"Ошибки при отрисовке: {render_errs}",
                         "text": "Игра много раз сообщала об ошибках в потоке отрисовки — часто это шейдеры или ресурспак с ошибкой.",
                         "fix": {"action": "logs", "arg": "", "label": "Открыть консоль"}})

    # --- долгая загрузка
    t = [(int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])) for m in (STARTED_RE.match(l) for l in lines) if m]
    stop = next((int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) for m in (re.match(r"^\[(\d\d):(\d\d):(\d\d)\] \[.*?\]: (?:Reloading ResourceManager|Created: \d+x\d+x\d+ minecraft:textures/atlas)", l) for l in lines) if m), 0)
    if t and stop and 0 < stop - t[0] < 3600 and stop - t[0] >= 90:
        findings.append({"level": "info", "title": f"Игра грузилась {stop - t[0]} с",
                         "text": "Долгая загрузка — обычно из-за большого числа модов или тяжёлых ресурспаков. Второй запуск быстрее (кэш), "
                                 "а на Java 19+ лаунчер ещё и кэширует классы.", "fix": None})

    order = {"bad": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: order[f["level"]])
    verdict = "bad" if any(f["level"] == "bad" for f in findings) else "warn" if any(f["level"] == "warn" for f in findings) else "ok"
    return {"findings": findings, "verdict": verdict, "behind": len(behind), "gc": gc, "has_gc_log": bool(gc["count"]),
            "has_game_log": bool(lines)}
