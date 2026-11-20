"""Предрелизная проверка одной командой: py tools/precheck.py

Запускает всё, что обязательно перед выпуском: компиляция Python, node --check для JS, серверные тесты (node --test),
тесты Python (в них же проверка перевода I18N_EN) и поиск забытых пометок «убрать после 1.0».
Код выхода 0 — всё хорошо, иначе 1 (и список, что не прошло)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEFTOVER_FILES = ["server/worker.js", "server/worlds.js", "server/relay/relay.py", "core/relay.py"]
LEFTOVER_MARK = "после 1.0"


def run(name: str, cmd: list[str]) -> bool:
    print(f"==> {name}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    ok = r.returncode == 0
    print(("    OK" if ok else f"    ПРОВАЛ (код {r.returncode})"), flush=True)
    return ok


def node_check() -> bool:
    files = [*ROOT.glob("web/js/**/*.js"), *ROOT.glob("server/**/*.js"), *ROOT.glob("tests/*.mjs")]
    files = [f for f in files if ".wrangler" not in f.parts and "node_modules" not in f.parts]
    bad = []
    print(f"==> node --check ({len(files)} файлов)", flush=True)
    for f in files:
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{f.relative_to(ROOT)}: {r.stderr.strip().splitlines()[0] if r.stderr else ''}")
    print("    OK" if not bad else "    ПРОВАЛ:\n    " + "\n    ".join(bad), flush=True)
    return not bad


def leftovers() -> bool:
    print("==> пометки «убрать после 1.0»", flush=True)
    found = []
    for rel in LEFTOVER_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if LEFTOVER_MARK in line and "уб" in line.lower():
                found.append(f"{rel}:{n}: {line.strip()[:100]}")
    print("    OK" if not found else "    ПРОВАЛ:\n    " + "\n    ".join(found), flush=True)
    return not found


def main() -> int:
    results = {
        "compileall": run("compileall", [sys.executable, "-m", "compileall", "-q", "core", "backend", "main.py", "tools"]),
        "node --check": node_check(),
        "node --test": run("node --test tests/", ["node", "--test", "tests/"]),
        "python tests": run("python tests (в т.ч. перевод)", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]),
        "пометки": leftovers(),
    }
    failed = [k for k, v in results.items() if not v]
    print("\nИТОГ:", "всё прошло" if not failed else "не прошло: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
