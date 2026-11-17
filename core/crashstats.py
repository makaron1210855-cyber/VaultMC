"""Анонимная статистика падений — только если игрок разрешил (настройка crash_stats, по умолчанию выключена).

Уходит одна короткая запись: тип проблемы (какое действие предложил лаунчер: ram_down, clear_jvm… или other),
версия лаунчера, версия игры и название загрузчика. Ни ника, ни путей, ни названий модов, ни адреса компьютера
в записи нет. Сервер лишь прибавляет единицу в счётчик «день + тип + версии».
"""
from __future__ import annotations

import json
import re
import threading
import urllib.request

from . import skinserver

KIND_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def kinds_of(analysis: list | None) -> list[str]:
    """Типы проблем из разбора вылета; нет находок — «unknown». Не больше трёх, без повторов."""
    out: list[str] = []
    for f in analysis or []:
        fix = f.get("fix") if isinstance(f, dict) else None
        k = str((fix or {}).get("action") or "other").lower()
        k = k if KIND_RE.match(k) else "other"
        if k not in out:
            out.append(k)
    return (out or ["unknown"])[:3]


def build(kind: str, app_version: str, mc: str, loader: str) -> dict:
    clean_mc = re.sub(r"[^0-9a-zA-Z.+_ -]", "", str(mc or ""))[:30]
    clean_loader = re.sub(r"[^a-z]", "", str(loader or "").lower())[:12]
    return {"kind": kind, "app": str(app_version), "mc": clean_mc, "loader": clean_loader}


def _post(report: dict, timeout: float = 8.0) -> bool:
    base = skinserver.server_url()
    if not base:
        return False
    req = urllib.request.Request(base + "/api/stat", data=json.dumps(report).encode("utf-8"), method="POST",
                                 headers={"content-type": "application/json", "user-agent": "VaultMC"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001  статистика — не повод что-то ломать; нет связи — просто не отправили
        return False


def send_async(analysis: list | None, app_version: str, mc: str, loader: str) -> None:
    reports = [build(k, app_version, mc, loader) for k in kinds_of(analysis)]

    def job():
        for r in reports:
            _post(r)
    threading.Thread(target=job, daemon=True).start()
