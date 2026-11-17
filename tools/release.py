"""
Сборка релиза VaultMC одной командой (Windows / Linux).

    python tools/release.py            — проверка кода → установщик → сайт → архивы
    python tools/release.py --check    — только показать план
    python tools/release.py --no-installer  — взять уже собранный dist/VaultMC-Setup.exe

Папки проекта:
    packaging/python/   встроенный Python 3.12 для Windows с pywebview и pythonnet (кладётся в установщик)
    installer/          vaultmc.nsi — сценарий установщика NSIS 3
    build/bundle/       сюда скрипт раскладывает app/ и python/ перед сборкой (временное)
    dist/               готовый VaultMC-Setup.exe
    site/               сайт для Cloudflare (воркер vaultmc): index.html, version.json, VaultMC-Setup.exe, img/
    releases/           готовые архивы: vaultmc-site-<версия>.zip и vaultmc_v<версия>.zip (исходники без ключа)

Номер версии — только в core/utils.py (APP_VERSION). Список изменений — core/changelog.py.
Загрузку в Cloudflare скрипт не делает: архив сайта заливается в воркер vaultmc вручную.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_RUNTIME = ROOT / "packaging" / "python"
NSI = ROOT / "installer" / "vaultmc.nsi"
BUNDLE = ROOT / "build" / "bundle"
EXE = ROOT / "dist" / "VaultMC-Setup.exe"
SITE = ROOT / "site"
OUT = ROOT / "releases"

MAJOR_TITLE = "⭐ VaultMC 1.0 — первый стабильный выпуск: светлая тема, масштаб, клавиатура, блокировка игроков, уведомления Windows, разбор подвисаний"

# что НЕ кладём в установщик (папка app)
APP_SKIP_TOP = {"build", "dist", "releases", "site", "packaging", "server", "tests", ".git", ".venv", "venv", ".claude",
                "CLAUDE.md", ".gitignore"}
# мусор, который не попадает никуда
EXCLUDE_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", "build", "dist", "releases", ".idea",
                ".vscode", ".pytest_cache", ".mypy_cache", "logs", "crash-reports", ".vaultmc", ".claude"}
EXCLUDE_FILES = ["*.pyc", "*.pyo", "*.log", "*.tmp", "*.part", "*.zip", "*.exe", ".DS_Store", "Thumbs.db",
                 ".env", "*.local.json", "config.json", "accounts.json", ".vaultmc-*"]
KEYS_TEMPLATE = '''"""
Ключи разработчика. В архиве исходников ключ убран — вставьте свой перед сборкой установщика.
Ключ CurseForge: https://console.curseforge.com → API keys (бесплатно). Не используйте чужие ключи.
"""

CURSEFORGE_API_KEY = ""

# Адрес сервера скинов VaultMC (server/worker.js на Cloudflare Workers). Пусто — без сервера.
SKIN_SERVER = "{skin}"
'''


def log(msg: str) -> None:
    print(f"[release] {msg}", flush=True)


def app_version() -> str:
    m = re.search(r'^APP_VERSION\s*=\s*"(\d+\.\d+(?:\.\d+)?)"', (ROOT / "core" / "utils.py").read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("Не нашёл APP_VERSION в core/utils.py")
    return m.group(1)


def _excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    return any(fnmatch.fnmatch(rel.name, p) for p in EXCLUDE_FILES)


def find_makensis() -> str:
    found = shutil.which("makensis")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base and (Path(base) / "NSIS" / "makensis.exe").exists():
            return str(Path(base) / "NSIS" / "makensis.exe")
    sys.exit("Не найден NSIS (makensis). Установите NSIS 3: https://nsis.sourceforge.io/Download")


# ------------------------------------------------------------------ 1. проверка кода
def preflight() -> None:
    import py_compile
    for f in ROOT.rglob("*.py"):
        rel = f.relative_to(ROOT)
        if not _excluded(rel) and rel.parts[0] not in ("packaging", "build"):
            py_compile.compile(str(f), doraise=True)
    node = shutil.which("node")
    if node:
        for f in (ROOT / "web" / "js").rglob("*.js"):
            r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
            if r.returncode:
                sys.exit(f"Ошибка в {f.relative_to(ROOT)}:\n{r.stderr}")
    else:
        log("node не найден — проверку JS пропускаю")
    keys = (ROOT / "core" / "keys.py").read_text(encoding="utf-8")
    if not re.search(r'^CURSEFORGE_API_KEY\s*=\s*"[^"]+"', keys, re.M):
        log("ВНИМАНИЕ: в core/keys.py пустой ключ CurseForge — каталог CurseForge у игроков работать не будет")
    log("проверка кода: ок")


# ------------------------------------------------------------------ 2. установщик
def check_path_length() -> None:
    """makensis не умеет пути длиннее 260 символов (MAX_PATH) и падает с непонятной ошибкой
    «failed opening file». Он открывает файлы как installer\\..\\build\\bundle\\..., так и считаем."""
    nsis_base = len(str(NSI.parent)) + len("\\..\\build\\bundle\\")
    longest, worst = 0, None
    for f in BUNDLE.rglob("*"):
        if f.is_file():
            n = nsis_base + len(str(f.relative_to(BUNDLE)))
            if n > longest:
                longest, worst = n, f
    if longest >= 260:
        sys.exit(f"Слишком длинный путь к папке проекта: у NSIS выходит {longest} символов (можно не больше 259).\n"
                 f"Самый длинный файл: {worst}\n"
                 f"Перенесите папку проекта ближе к корню диска, например C:\\VaultMC, и запустите сборку снова.\n"
                 f"Сейчас проект лежит в: {ROOT}")


def build_installer(version: str) -> Path:
    if not (PY_RUNTIME / "python.exe").exists():
        sys.exit(f"Нет встроенного Python: {PY_RUNTIME}")
    shutil.rmtree(BUNDLE, ignore_errors=True)
    app = BUNDLE / "app"
    app.mkdir(parents=True)
    for item in ROOT.iterdir():                      # всё приложение целиком, а не только core/backend/web
        if item.name in APP_SKIP_TOP or _excluded(Path(item.name)):
            continue
        if item.is_dir():
            shutil.copytree(item, app / item.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log"))
        else:
            shutil.copy2(item, app / item.name)
    sys.path.insert(0, str(ROOT))
    from core.integrity import build as build_integrity   # files.json: лаунчер сверяет свои файлы при запуске
    log(f"контрольные суммы файлов лаунчера: {build_integrity(app, version)}")
    shutil.copytree(PY_RUNTIME, BUNDLE / "python", ignore=shutil.ignore_patterns("__pycache__"))
    # копия интерпретатора с названием и логотипом: диспетчер задач, панель задач и «Открыть с помощью»
    # показывают «VaultMC» со своим значком, а не «Python»
    from exeicon import make_branded_copy
    make_branded_copy(BUNDLE / "python" / "pythonw.exe", ROOT / "assets" / "logo_win.ico",
                      BUNDLE / "python" / "VaultMC.exe", version)
    check_path_length()
    EXE.parent.mkdir(parents=True, exist_ok=True)
    EXE.unlink(missing_ok=True)
    # /INPUTCHARSET UTF8: файл установщика — UTF-8 без BOM; без этого NSIS читал его в кодировке Windows,
    # и все русские надписи (окно установки, ярлыки, «Открыть с помощью») выходили кракозябрами
    nsis_version = version if version.count(".") == 2 else version + ".0"   # NSIS требует X.X.X: «7.0» → «7.0.0»
    r = subprocess.run([find_makensis(), "-INPUTCHARSET", "UTF8", "-V2", f"-DVERSION={nsis_version}", NSI.name], cwd=NSI.parent,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or not EXE.exists():
        sys.exit(f"NSIS не собрал установщик:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    log(f"установщик: {EXE.stat().st_size / 1048576:.1f} МБ")
    return EXE


# ------------------------------------------------------------------ 3. сайт
def build_site(version: str, exe: Path) -> Path:
    sys.path.insert(0, str(ROOT))
    from core.changelog import CHANGELOG, notes_since    # noqa: E402

    shutil.copy2(exe, SITE / "VaultMC-Setup.exe")
    data = exe.read_bytes()
    page = SITE / "index.html"
    s = page.read_text(encoding="utf-8")
    s = re.sub(r'(<span id="siteVer">)[^<]*(</span>)', rf"\g<1>{version}\g<2>", s)
    page.write_text(s, encoding="utf-8")
    import siteversions                                # страница версий: все выпуски из changelog.py
    siteversions.write(SITE, CHANGELOG, version)
    major = int(version.split(".")[0])
    changes = {v: lines for v, lines in CHANGELOG.items() if int(v.split(".")[0]) >= major - 2}
    info = {"version": version, "url": "VaultMC-Setup.exe", "sha256": hashlib.sha256(data).hexdigest(),
            "notes": f"{MAJOR_TITLE}\n{notes_since(None, version)}", "changes": changes}
    from signing import sign_manifest                 # подпись ключом владельца (tools/signing.py)
    info = sign_manifest(info)
    (SITE / "version.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    site_zip = OUT / f"vaultmc-site-{version}.zip"
    site_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(site_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SITE.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(SITE).as_posix())
    with zipfile.ZipFile(site_zip) as z:            # контроль: версия, контрольная сумма и подпись сходятся
        info2 = json.loads(z.read("version.json"))
        from core.updater import signature_ok
        ok = (info2["version"] == version and f'siteVer">{version}<' in z.read("index.html").decode("utf-8")
              and hashlib.sha256(z.read("VaultMC-Setup.exe")).hexdigest() == info2["sha256"]
              and signature_ok(info2) and "versions.html" in z.namelist())
    if not ok:
        sys.exit(f"Архив сайта собран неправильно: {site_zip}")
    log(f"сайт: {site_zip.name} ({site_zip.stat().st_size / 1048576:.1f} МБ)")
    return site_zip


# ------------------------------------------------------------------ 4. архив исходников (без ключа)
def build_source(version: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"vaultmc_v{version}.zip"
    path.unlink(missing_ok=True)
    keys_text = (ROOT / "core" / "keys.py").read_text(encoding="utf-8")
    m = re.search(r'^SKIN_SERVER\s*=\s*"([^"]*)"', keys_text, re.M)
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ROOT.rglob("*")):
            rel = f.relative_to(ROOT)
            if not f.is_file() or _excluded(rel) or rel.parts[0] in ("packaging", "site"):
                continue
            arc = f"vaultmc_v{version}/{rel.as_posix()}"
            if rel.as_posix() == "core/keys.py":
                z.writestr(arc, KEYS_TEMPLATE.format(skin=m.group(1) if m else ""))
            else:
                z.write(f, arc)
            count += 1
    real = re.search(r'^CURSEFORGE_API_KEY\s*=\s*"([^"]+)"', keys_text, re.M)
    if real:
        with zipfile.ZipFile(path) as z:
            if any(real.group(1).encode() in z.read(n) for n in z.namelist()):
                path.unlink()
                sys.exit("В архив исходников попал ключ CurseForge — архив удалён")
    log(f"исходники: {path.name} ({count} файлов, ключ убран)")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка релиза VaultMC")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-installer", action="store_true")
    args = ap.parse_args()
    version = app_version()
    log(f"версия {version} (core/utils.py)")
    if args.check:
        log(f"будет: {EXE.relative_to(ROOT)}, releases/vaultmc-site-{version}.zip, releases/vaultmc_v{version}.zip")
        return
    preflight()
    exe = EXE if args.no_installer and EXE.exists() else build_installer(version)
    site_zip = build_site(version, exe)
    build_source(version)
    print("\nГотово. Дальше вручную:\n"
          f"  1. Распаковать releases/{site_zip.name} в пустую папку → Cloudflare, воркер vaultmc → загрузить, "
          "удалив старые файлы → Deploy\n"
          "  2. Если менялся server/worker.js — вставить его в воркер vaultmc-skins → Deploy\n"
          "  Архив vaultmc_v*.zip (исходники) на сайт НЕ заливать.")


if __name__ == "__main__":
    main()
