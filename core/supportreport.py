"""Отчёт для поддержки: один архив с журналами и сведениями о ПК и сборке — личное вырезается.

Что внутри: report.txt (версии, ПК, Java, сборка, моды, настройки), журналы лаунчера и игры, последний crash-report,
журнал сборщика мусора, результат проверки сети. Чего внутри НЕТ никогда: accounts.json, skinserver.json, config.json целиком,
токены и ключи. Архив никуда не отправляется — игрок сам передаёт файл тому, кому хочет.

Вырезается (redact): имя пользователя Windows в путях и тексте, ник аккаунта (по желанию), адреса почты, IP-адреса,
токены доступа (в том числе из строки запуска игры), JWT, ключи CurseForge, длинные шестнадцатеричные строки (ключи сервера).
"""
from __future__ import annotations

import io
import os
import re
import time
import zipfile
from pathlib import Path

TAIL_LAUNCHER = 400_000
TAIL_GAME = 1_000_000
TAIL_SMALL = 300_000

# настройки, которые безопасно показать: только то, что нужно, чтобы понять проблему
SAFE_CONFIG = ("ram_gb", "optimize_jvm", "high_priority", "discrete_gpu", "java_auto", "fullscreen", "window_width",
               "window_height", "tunnel_mod", "download_threads", "skins_in_game", "show_online", "auto_world_backups", "accent")

# имя папки пользователя; в Windows оно бывает с пробелом («Ivan Petrov»), поэтому сначала берём всё до следующего слэша
_USER_PATH = re.compile(r"(?i)((?:[A-Za-z]:)?[\\/](?:Users|home)[\\/])(?:[^\\/\"':,;()<>\r\n]{1,40}?(?=[\\/])|[^\\/\s\"':,;()<>]+)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_IPV4 = re.compile(r"(?<![\w.\-+])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d.]*\d|\.\w)")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/]+=*")
_ARG_SECRET = re.compile(r"(?i)(--(?:accessToken|clientId|xuid|session)[ =])\S+")
_KV_SECRET = re.compile(r"(?i)((?:access|refresh|client)?[_-]?token|password|passwd|secret|api[_-]?key)(\"?\s*[:=]\s*\"?)[^\s\",;}]+")
_JWT = re.compile(r"\beyJ[\w\-]{6,}\.[\w\-]{6,}\.[\w\-]{6,}\b")
_CF_KEY = re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{30,}")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def redact(text: str, names: tuple[str, ...] = (), stats: dict | None = None) -> str:
    """Убирает личное из текста. names — слова, которые надо скрыть как есть (имя пользователя Windows, ник)."""
    def count(key, n=1):
        if stats is not None:
            stats[key] = stats.get(key, 0) + n

    def sub(pattern, repl, s, key):
        new, n = pattern.subn(repl, s)
        if n:
            count(key, n)
        return new

    text = sub(_JWT, "<токен>", text, "токены")
    text = sub(_BEARER, "Bearer <токен>", text, "токены")
    text = sub(_ARG_SECRET, r"\1<скрыто>", text, "токены")
    text = sub(_KV_SECRET, r"\1\2<скрыто>", text, "токены")
    text = sub(_CF_KEY, "<ключ>", text, "ключи")
    text = sub(_HEX, "<hex>", text, "ключи")
    text = sub(_EMAIL, "<почта>", text, "почта")
    text = sub(_IPV4, lambda m: m.group(0) if m.group(1).startswith(("127.", "0.", "255.")) else "<ip>", text, "ip")
    text = sub(_USER_PATH, lambda m: m.group(1) + "<пользователь>", text, "пути")
    for name in sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True):
        text = sub(re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.I), "<имя>", text, "имена")
    return text


def _tail(path: Path, limit: int) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read().decode("utf-8", errors="replace")
        return ("…(начало пропущено)…\n" if size > limit else "") + data
    except OSError:
        return ""


def _newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return files[-1] if files else None


SUPPORT_CONTACTS = ("Поддержка VaultMC: Telegram n0xam1 (https://t.me/n0xam1), Discord n0xam1",)


def summary(info: dict) -> str:
    """report.txt — сведения, собранные лаунчером (info) в читаемом виде."""
    out = [f"Отчёт VaultMC {info.get('launcher', '?')} · {time.strftime('%Y-%m-%d %H:%M')}", *SUPPORT_CONTACTS, ""]
    for title, rows in info.get("sections", []):
        out.append(f"== {title}")
        out += [f"  {r}" for r in rows]
        out.append("")
    return "\n".join(out)


def collect(game_dir: Path, launcher_home: Path, info: dict, console: list[str] | None = None,
            names: tuple[str, ...] = (), net_text: str = "") -> tuple[dict[str, str], dict]:
    """Файлы отчёта (имя → текст, уже очищенный) и сколько чего вырезано."""
    stats: dict = {}
    raw: dict[str, str] = {"report.txt": summary(info)}
    raw["launcher.log"] = _tail(launcher_home / "launcher.log", TAIL_LAUNCHER)
    if console:
        raw["console.txt"] = "\n".join(console[-1500:])
    raw["game-latest.log"] = _tail(game_dir / "logs" / "latest.log", TAIL_GAME)
    raw["gc.log"] = _tail(game_dir / "logs" / "vaultmc-gc.log", TAIL_SMALL)
    crash = _newest(game_dir / "crash-reports", "crash-*.txt")
    if crash:
        raw[f"crash-reports/{crash.name}"] = _tail(crash, TAIL_SMALL)
    hs = _newest(game_dir, "hs_err_pid*.log")
    if hs:
        raw[hs.name] = _tail(hs, TAIL_SMALL)
    if net_text:
        raw["network-check.txt"] = net_text
    files = {name: redact(text, names, stats) for name, text in raw.items() if text.strip() and name != "report.txt"}
    report = redact(raw["report.txt"], names, stats)
    head = SUPPORT_CONTACTS[0]
    for marker in (head, redact(head, names)):          # вернуть строку контактов, если очистка её изменила
        if marker in report:
            report = report.replace(marker, head, 1)
            break
    files = {"report.txt": report, **files}
    return files, stats


def build_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def windows_names() -> tuple[str, ...]:
    """Имя пользователя Windows и имя компьютера — их тоже скрываем в тексте, не только в путях."""
    out = []
    for v in (os.environ.get("USERNAME"), os.environ.get("COMPUTERNAME")):
        if v and len(v) >= 3:
            out.append(v)
    return tuple(out)
