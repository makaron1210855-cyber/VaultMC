"""
Облако миров — запросы к серверу VaultMC (раздел 6 дизайна docs/world-sync-design.md).

  POST /api/worlds        {name, token, action, v: 1, …} — через ключи skinserver (повтор при 403 auth)
  PUT  /api/worlds/part   байты части; ключа ника в заголовках нет, правом служит sid (правило 6)
  POST /api/worlds/file   {name, token, wid, rid, offset} → поток байт (докачка — через offset)
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import skinserver
from .net import USER_AGENT
from .skinserver import SkinServerError


class CloudError(Exception):
    """status 0 — нет связи; code — поле code ответа сервера (auth, conflict, busy, quota, full, too_big, gone, …)."""

    def __init__(self, message: str, status: int = 0, code: str = "", data: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.data = data or {}


def _from_skin(exc: SkinServerError) -> CloudError:
    return CloudError(str(exc), exc.status, exc.code)


def call(nick: str, kind: str, action: str, **payload) -> dict:
    """Действие облака от имени ника. Для оффлайн-аккаунта ник Ely.by не доказываем (prove=False)."""
    body = {"action": action, "v": 1, **payload}
    prove = kind == "elyby"
    try:
        try:
            return skinserver._authed(nick, "/api/worlds", body, prove)
        except SkinServerError as exc:
            if exc.status == 403 and exc.code == "ely_bind" and prove and skinserver.ELY_PROVER:
                proof = skinserver.ELY_PROVER(nick)                      # запись Ely.by без elyId: подтвердить заново
                if not proof:
                    raise
                skinserver._call("/api/register", {"name": nick, "token": skinserver.ensure_registered(nick, True),
                                                   "ely": proof, "bind": True})
                return skinserver._authed(nick, "/api/worlds", body, prove)
            raise
    except SkinServerError as exc:
        raise _from_skin(exc) from None
    except OSError as exc:
        raise CloudError(f"Нет связи с сервером VaultMC: {exc}") from None


def put_part(sid: str, n: int, data: bytes, md5: str, progress=None) -> str:
    """Отправить часть архива. Возвращает etag. Content-Length обязателен (R2 принимает поток только известной длины)."""
    url = urllib.parse.urlsplit(skinserver.server_url() + "/api/worlds/part")
    conn_cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(url.netloc, timeout=120)
    try:
        conn.putrequest("PUT", url.path)
        for k, v in {"User-Agent": USER_AGENT, "Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
                     "X-VMC-Upload": sid, "X-VMC-Part": str(n), "X-VMC-MD5": md5}.items():
            conn.putheader(k, v)
        conn.endheaders()
        step = 1 << 20
        for i in range(0, len(data), step):
            conn.send(data[i:i + step])
            if progress:
                progress(min(len(data), i + step))
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as exc:
        raise CloudError(f"Нет связи с сервером VaultMC: {exc}") from None
    finally:
        conn.close()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = {}
    if resp.status != 200:
        raise CloudError(str(body.get("error") or f"Сервер ответил {resp.status}"), resp.status, str(body.get("code") or ""), body)
    return str(body.get("etag") or "")


def _file_request(nick: str, kind: str, wid: str, rid: str, offset: int):
    token = skinserver.ensure_registered(nick, kind == "elyby")
    data = json.dumps({"name": nick, "token": token, "wid": wid, "rid": rid, "offset": offset}).encode()
    req = urllib.request.Request(skinserver.server_url() + "/api/worlds/file", data=data, method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=60)


def download(nick: str, kind: str, wid: str, rid: str, dest: Path, size: int, progress=None, cancelled=lambda: False) -> None:
    """Скачать версию в dest (с докачкой по offset, 3 попытки). Проверка sha256 — на вызывающем."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _attempt in range(3):
        have = dest.stat().st_size if dest.exists() else 0
        if have > size:
            dest.unlink()
            have = 0
        try:
            try:
                resp = _file_request(nick, kind, wid, rid, have)
            except urllib.error.HTTPError as exc:
                if exc.code == 403:                      # ключ не узнан: регистрируемся заново и повторяем один раз
                    skinserver._registered.discard(nick.lower())
                    resp = _file_request(nick, kind, wid, rid, have)
                else:
                    raise
            with resp:
                if int(resp.headers.get("x-vmc-size", size)) != size:
                    raise CloudError("Версия в облаке изменилась — обновите список", 409, "conflict")
                with open(dest, "ab" if have else "wb") as fh:
                    for chunk in iter(lambda: resp.read(1 << 20), b""):
                        if cancelled():
                            raise CloudError("Остановлено", 0, "cancelled")
                        fh.write(chunk)
                        have += len(chunk)
                        if progress:
                            progress(have, size)
            if have == size:
                return
            raise OSError("обрыв при скачивании")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", "replace"))
            except ValueError:
                body = {}
            raise CloudError(str(body.get("error") or f"Сервер ответил {exc.code}"), exc.code, str(body.get("code") or ""), body) from None
        except (OSError, http.client.HTTPException) as exc:
            last = exc
    raise CloudError(f"Нет связи с сервером VaultMC: {last}")
