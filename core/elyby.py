"""
Ely.by — бесплатная система аккаунтов и скинов для Minecraft (лицензия не нужна).
Протокол совместим с Yggdrasil: https://docs.ely.by/ru/minecraft-auth.html
Регистрация аккаунта — на сайте https://account.ely.by/register

В игре Ely.by работает через authlib-injector (см. authlib.py): скины видны всем игрокам с Ely.by,
а на серверах с Ely.by проходит авторизация.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse

from .accounts import Account, AuthError
from .net import HttpError, get_json, post_form, post_json

AUTH_SERVER = "https://authserver.ely.by"
REGISTER_URL = "https://account.ely.by/register"

# Вход через сайт Ely.by (OAuth 2.0 + PKCE): лаунчер не получает пароль.
# Client ID — публичный (секрета у публичного клиента нет), его задал владелец в account.ely.by.
OAUTH_CLIENT_ID = "vaultmc"
OAUTH_AUTHORIZE = "https://account.ely.by/oauth2/v1"
OAUTH_TOKEN = "https://account.ely.by/api/oauth2/v1/token"
OAUTH_INFO = "https://account.ely.by/api/account/v1/info"
OAUTH_SCOPES = "account_info minecraft_server_session offline_access"
OAUTH_TIMEOUT = 240  # секунд ждём, пока пользователь войдёт на сайте


class TwoFactorRequired(AuthError):
    """У аккаунта включена двухфакторная защита — нужен код из приложения."""


def _format_uuid(raw: str) -> str:
    raw = raw.replace("-", "")
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def login(username: str, password: str, totp: str = "") -> Account:
    client_token = secrets.token_hex(16)
    payload = {"username": username.strip(), "password": f"{password}:{totp}" if totp else password,
               "clientToken": client_token, "requestUser": True}
    try:
        data = post_json(f"{AUTH_SERVER}/auth/authenticate", payload)
    except HttpError as exc:
        message = exc.body.get("errorMessage", "") if isinstance(exc.body, dict) else ""
        if "two factor" in message.lower():
            raise TwoFactorRequired("Введите код двухфакторной аутентификации") from None
        if exc.status in (401, 403):
            raise AuthError("Неверный логин или пароль Ely.by") from None
        raise AuthError(f"Ely.by: {exc}") from None
    except OSError as exc:
        raise AuthError(f"Нет связи с Ely.by: {exc}") from None

    profile = data.get("selectedProfile") or {}
    if not profile:
        raise AuthError("У аккаунта Ely.by нет игрового профиля")
    acc = Account(id=secrets.token_hex(6), kind="elyby", username=profile["name"], uuid=_format_uuid(profile["id"]),
                  client_token=data.get("clientToken", client_token))
    acc.set_tokens(data["accessToken"])
    return acc


_DONE_PAGE = ("<!doctype html><html lang='ru'><meta charset='utf-8'><title>VaultMC</title>"
              "<body style='font-family:sans-serif;text-align:center;padding:60px'>"
              "<h2>Готово</h2><p>Вход выполнен. Вернитесь в лаунчер VaultMC и закройте эту вкладку.</p></body></html>")


def _pkce() -> tuple[str, str]:
    """PKCE: секретная строка (verifier) и её отпечаток (challenge) — защита кода входа без секрета клиента."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def oauth_login(open_url, timeout: int = OAUTH_TIMEOUT) -> Account:
    """Вход через сайт Ely.by. open_url — функция, открывающая страницу входа в браузере.
    Лаунчер поднимает временный адрес на этом компьютере (127.0.0.1), куда сайт вернёт код входа."""
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    got: dict = {}
    arrived = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — имя задаёт http.server
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            got.update(dict(urllib.parse.parse_qsl(parts.query)))
            page = _DONE_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            arrived.set()

        def log_message(self, *args):  # без записи в консоль
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", 0), Callback)
    except OSError as exc:
        raise AuthError(f"Не удалось открыть адрес для входа на этом компьютере: {exc}") from None
    redirect = f"http://127.0.0.1:{server.server_address[1]}/callback"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        query = urllib.parse.urlencode({"client_id": OAUTH_CLIENT_ID, "redirect_uri": redirect,
                                        "response_type": "code", "scope": OAUTH_SCOPES, "state": state,
                                        "code_challenge": challenge, "code_challenge_method": "S256"})
        open_url(f"{OAUTH_AUTHORIZE}?{query}")
        deadline = time.time() + timeout
        while not arrived.is_set():
            if time.time() > deadline:
                raise AuthError("Вход на сайте Ely.by не завершён за 4 минуты — попробуйте ещё раз")
            arrived.wait(0.5)
    finally:
        server.shutdown()
        server.server_close()

    if "error" in got:
        raise AuthError(got.get("error_message") or "Ely.by отклонил вход")
    if got.get("state") != state or not got.get("code"):
        raise AuthError("Ответ Ely.by не совпал с запросом — войдите ещё раз")

    try:
        token = post_form(OAUTH_TOKEN, {"client_id": OAUTH_CLIENT_ID, "redirect_uri": redirect,
                                        "grant_type": "authorization_code", "code": got["code"],
                                        "code_verifier": verifier})
        info = get_json(OAUTH_INFO, headers={"Authorization": f"Bearer {token['access_token']}"}, attempts=2)
    except HttpError as exc:
        raise AuthError(f"Ely.by не выдал доступ: {exc}") from None
    except (OSError, KeyError) as exc:
        raise AuthError(f"Нет связи с Ely.by: {exc}") from None

    if not info.get("uuid") or not info.get("username"):
        raise AuthError("У аккаунта Ely.by нет игрового профиля")
    acc = Account(id=secrets.token_hex(6), kind="elyby", username=info["username"],
                  uuid=_format_uuid(info["uuid"]), client_token=secrets.token_hex(16))
    acc.set_tokens(token["access_token"], token.get("refresh_token", ""), float(token.get("expires_in") or 0))
    return acc


def ensure_valid(account: Account) -> None:
    """Проверяет токен перед запуском и при необходимости обновляет его без пароля."""
    try:
        post_json(f"{AUTH_SERVER}/auth/validate", {"accessToken": account.token()}, timeout=8)
        return
    except HttpError:
        pass
    except Exception:  # noqa: BLE001 — нет интернета / Ely.by не отвечает: играем со старым токеном (одиночная игра работает)
        return
    if account.refresh_token:  # вход через сайт Ely.by: обновляем по refresh-токену OAuth
        try:
            data = post_form(OAUTH_TOKEN, {"client_id": OAUTH_CLIENT_ID, "grant_type": "refresh_token",
                                           "refresh_token": account.refresh(), "scope": OAUTH_SCOPES})
        except HttpError:
            raise AuthError("Сессия Ely.by истекла — войдите в аккаунт заново") from None
        except Exception:  # noqa: BLE001
            return
        account.set_tokens(data["access_token"], data.get("refresh_token", ""), float(data.get("expires_in") or 0))
        return
    try:
        data = post_json(f"{AUTH_SERVER}/auth/refresh", {"accessToken": account.token(),
                                                         "clientToken": account.client_token, "requestUser": True}, timeout=10)
    except HttpError:
        raise AuthError("Сессия Ely.by истекла — войдите в аккаунт заново") from None
    except Exception:  # noqa: BLE001
        return
    account.set_tokens(data["accessToken"])
    if data.get("selectedProfile", {}).get("name"):
        account.username = data["selectedProfile"]["name"]


def prove_ownership(account: Account) -> str:
    """«Вход на сервер» через Ely.by со случайным serverId — сервер скинов проверит его через hasJoined
    и убедится, что ник действительно этого игрока. Возвращает serverId (или пусто, если не вышло)."""
    server_id = secrets.token_hex(16)
    try:
        ensure_valid(account)
        post_json(f"{AUTH_SERVER}/session/join", {"accessToken": account.token(),
                                                  "selectedProfile": account.uuid.replace("-", ""),
                                                  "serverId": server_id}, timeout=10)
        return server_id
    except Exception:  # noqa: BLE001
        return ""
