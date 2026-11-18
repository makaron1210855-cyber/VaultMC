"""Сеть: JSON-запросы и скачивание файлов с проверкой SHA-1 (только стандартная библиотека)."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import ssl
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from .utils import APP_VERSION

USER_AGENT = f"VaultMC/{APP_VERSION}"


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    """Пользователь нажал «Отмена»."""


class HttpError(Exception):
    """Ответ сервера с кодом ошибки. body — разобранный JSON (если удалось)."""

    def __init__(self, status: int, body):
        self.status = status
        self.body = body
        message = body.get("errorMessage") or body.get("error_description") or body.get("error") \
            if isinstance(body, dict) else str(body)[:200]
        super().__init__(f"HTTP {status}: {message}")


def _request(url: str, data: bytes | None = None, headers: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})


def request_json(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 20):
    """GET (data=None) или POST. Ошибки HTTP превращаются в HttpError с телом ответа."""
    try:
        with urllib.request.urlopen(_request(url, data, headers), timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        raise HttpError(exc.code, body) from None


def get_json(url: str, timeout: int = 20, headers: dict | None = None, attempts: int = 3):
    """GET с повторами: сеть иногда «моргает» (обрыв, таймаут, сервер перегружен) — пробуем ещё раз,
    чтобы кнопки срабатывали с первого нажатия. Ошибки 4xx (кроме 429) не повторяем — они не пройдут сами."""
    import time
    for attempt in range(attempts):
        try:
            return request_json(url, headers=headers, timeout=timeout)
        except HttpError as exc:
            if exc.status != 429 and exc.status < 500 or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException,
                json.JSONDecodeError, UnicodeDecodeError):
            if attempt == attempts - 1:
                raise
        time.sleep(0.6 * (attempt + 1) ** 2)       # 0.6 с, потом 2.4 с


def post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 20):
    return request_json(url, json.dumps(payload).encode("utf-8"),
                        {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}, timeout)


def post_form(url: str, fields: dict, timeout: int = 20):
    from urllib.parse import urlencode
    return request_json(url, urlencode(fields).encode("utf-8"),
                        {"Content-Type": "application/x-www-form-urlencoded"}, timeout)


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def is_valid(path: Path, sha1: str | None, size: int | None) -> bool:
    """Файл считается целым, если совпадает размер (быстро) или, когда размер неизвестен, SHA-1."""
    if not path.exists():
        return False
    if size is not None:
        return path.stat().st_size == size
    if sha1:
        return sha1_file(path) == sha1.lower()
    return True


_local = threading.local()

# Счётчик скачанных байт — для показа скорости («12.4 МБ/с») в прогрессе установки
_bytes_lock = threading.Lock()
_bytes_total = 0


def bytes_downloaded() -> int:
    return _bytes_total


def _count(n: int) -> None:
    global _bytes_total
    with _bytes_lock:
        _bytes_total += n
_SSL = ssl.create_default_context()


def _proxy_in_use() -> bool:
    return any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"))


def _pooled_get(url: str, timeout: int = 30):
    """
    GET через постоянное соединение (keep-alive) — своё для каждого потока и сервера.
    Тысячи мелких файлов (ассеты) качаются в разы быстрее, чем с новым соединением на каждый.
    """
    parts = urllib.parse.urlsplit(url)
    pool = getattr(_local, "pool", None)
    if pool is None:
        pool = _local.pool = {}
    key = (parts.scheme, parts.netloc)
    conn = pool.get(key)
    if conn is None:
        conn = (http.client.HTTPSConnection(parts.netloc, timeout=timeout, context=_SSL) if parts.scheme == "https"
                else http.client.HTTPConnection(parts.netloc, timeout=timeout))
        pool[key] = conn
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Connection": "keep-alive"})
        return conn.getresponse()
    except Exception:
        conn.close()
        pool.pop(key, None)
        raise


def _drop_connection(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    pool = getattr(_local, "pool", {}) or {}
    conn = pool.pop((parts.scheme, parts.netloc), None)
    if conn:
        conn.close()


class _NoRetry(Exception):
    pass


def download(url: str, dest: Path, sha1: str | None = None, size: int | None = None,
             cancel: threading.Event | None = None, retries: int = 4) -> bool:
    """Скачивает файл. Возвращает False, если файл уже был на месте."""
    if is_valid(dest, sha1, size):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".{threading.get_ident()}.part")
    last_error: Exception | None = None
    pooled = urllib.parse.urlsplit(url).scheme in ("http", "https") and not _proxy_in_use()
    for _ in range(retries):
        current = url
        try:
            h = hashlib.sha1()
            if pooled:
                for _hop in range(6):  # переадресации (например, скины Ely.by)
                    response = _pooled_get(current)
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        response.read()
                        current = urllib.parse.urljoin(current, location)
                        continue
                    break
                if response.status != 200:
                    response.read()
                    if response.status in (403, 404, 410):   # файла нет — повторять бесполезно
                        tmp.unlink(missing_ok=True)
                        raise _NoRetry(f"HTTP {response.status}")
                    raise DownloadError(f"HTTP {response.status}")
                reader = response
            else:
                reader = urllib.request.urlopen(_request(current), timeout=30)
            with open(tmp, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    chunk = reader.read(1 << 16)
                    if not chunk:
                        break
                    h.update(chunk)
                    f.write(chunk)
                    _count(len(chunk))
            if not pooled:
                reader.close()
            if sha1 and h.hexdigest() != sha1.lower():
                raise DownloadError(f"контрольная сумма не совпала ({dest.name})")
            os.replace(tmp, dest)
            return True
        except Cancelled:
            tmp.unlink(missing_ok=True)
            if pooled:                     # ответ дочитан не до конца — это соединение больше не годится
                _drop_connection(current)
            raise
        except _NoRetry as exc:
            raise DownloadError(f"не удалось скачать {url}: {exc}") from None
        except Exception as exc:  # noqa: BLE001 — сеть/диск/битый ответ: пробуем ещё раз с новым соединением
            last_error = exc
            if pooled:
                _drop_connection(current)
    tmp.unlink(missing_ok=True)
    raise DownloadError(f"не удалось скачать {url}: {last_error}")


def download_any(urls: list[str], dest: Path, sha1: str | None = None, size: int | None = None,
                 cancel: threading.Event | None = None) -> bool:
    """Как download, но с запасными ссылками: первая не отвечает — берём следующую."""
    last: Exception | None = None
    for i, url in enumerate(urls):
        try:
            return download(url, dest, sha1, size, cancel, retries=3 if i == 0 else 2)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise DownloadError(str(last) if last else f"нет ссылки для {dest.name}")


def download_parallel(jobs: list[tuple[list[str], Path, str | None, int | None]], log=print, title: str = "",
                      threads: int = 8, cancel: threading.Event | None = None) -> list[str]:
    """
    Качает много файлов одновременно (моды сборки и т.п.). jobs: (ссылки, куда, sha1, размер).
    Возвращает список ошибок (пустой — всё скачалось). Пишет в лог прогресс и скорость.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not jobs:
        return []
    errors: list[str] = []
    started, b0 = time.time(), bytes_downloaded()
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(threads, len(jobs)))) as pool:
        futures = {pool.submit(download_any, urls, dest, sha1, size, cancel): dest for urls, dest, sha1, size in jobs}
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Cancelled:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{futures[fut].name}: {exc}")
            if done == len(jobs) or done % 5 == 0:
                log(f"[{title or 'Загрузка'}] {done}/{len(jobs)} · {format_speed(bytes_downloaded() - b0, time.time() - started)}")
    return errors


def format_speed(nbytes: int, seconds: float) -> str:
    mb = nbytes / 1048576 / max(seconds, 0.001)
    return f"{mb:.1f} МБ/с" if mb >= 1 else f"{mb * 1024:.0f} КБ/с"
