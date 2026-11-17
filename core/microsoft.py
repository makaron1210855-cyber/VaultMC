"""
Вход через Microsoft (лицензия Minecraft) — НЕОБЯЗАТЕЛЬНО, всё остальное работает без него.

Схема: код устройства Microsoft -> Xbox Live -> XSTS -> Minecraft Services.
Нужен Client ID собственного приложения Azure (Microsoft Entra) с доступом к Minecraft API —
Mojang выдаёт доступ новым приложениям по заявке. Без Client ID этот вход недоступен (см. README).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from .accounts import Account, AuthError
from .net import HttpError, get_json, post_form, post_json

TENANT = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
SCOPE = "XboxLive.signin offline_access"


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float


def start_device_login(client_id: str) -> DeviceCode:
    if not client_id:
        raise AuthError("Не задан Client ID приложения Microsoft (Настройки → Аккаунты)")
    data = post_form(f"{TENANT}/devicecode", {"client_id": client_id, "scope": SCOPE})
    return DeviceCode(data["device_code"], data["user_code"], data["verification_uri"],
                      int(data.get("interval", 5)), time.time() + int(data.get("expires_in", 900)))


def wait_for_login(client_id: str, code: DeviceCode, cancelled=lambda: False) -> Account:
    """Ждёт, пока пользователь введёт код на сайте Microsoft. Вызывать в фоновом потоке."""
    interval = code.interval
    while time.time() < code.expires_at:
        if cancelled():
            raise AuthError("Вход отменён")
        time.sleep(interval)
        try:
            token = post_form(f"{TENANT}/token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                                  "client_id": client_id, "device_code": code.device_code})
            return _finish(token)
        except HttpError as exc:
            error = exc.body.get("error") if isinstance(exc.body, dict) else ""
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise AuthError(f"Microsoft: {error or exc}") from None
    raise AuthError("Время на ввод кода истекло")


def refresh(client_id: str, account: Account) -> None:
    if account.expires_at - time.time() > 300:
        return
    try:
        token = post_form(f"{TENANT}/token", {"grant_type": "refresh_token", "client_id": client_id,
                                              "refresh_token": account.refresh(), "scope": SCOPE})
    except HttpError:
        raise AuthError("Сессия Microsoft истекла — войдите заново") from None
    fresh = _finish(token)
    account.access_token, account.refresh_token, account.expires_at = fresh.access_token, fresh.refresh_token, fresh.expires_at


def _finish(ms_token: dict) -> Account:
    xbl = post_json("https://user.auth.xboxlive.com/user/authenticate", {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": f"d={ms_token['access_token']}"},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
    uhs = xbl["DisplayClaims"]["xui"][0]["uhs"]
    try:
        xsts = post_json("https://xsts.auth.xboxlive.com/xsts/authorize", {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
            "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
    except HttpError as exc:
        xerr = exc.body.get("XErr") if isinstance(exc.body, dict) else None
        reasons = {2148916233: "У аккаунта нет профиля Xbox — создайте его на xbox.com",
                   2148916238: "Детский аккаунт: нужно добавить его в семью Microsoft"}
        raise AuthError(reasons.get(xerr, f"Xbox Live: {exc}")) from None
    try:
        mc = post_json("https://api.minecraftservices.com/authentication/login_with_xbox",
                       {"identityToken": f"XBL3.0 x={uhs};{xsts['Token']}"})
    except HttpError as exc:
        if exc.status == 403:
            raise AuthError("Minecraft API отклонил Client ID: приложению нужен доступ от Mojang") from None
        raise
    try:
        profile = get_json("https://api.minecraftservices.com/minecraft/profile",
                           headers={"Authorization": f"Bearer {mc['access_token']}"})
    except HttpError:
        raise AuthError("На этом аккаунте Microsoft нет лицензии Minecraft Java") from None
    acc = Account(id=secrets.token_hex(6), kind="microsoft", username=profile["name"], uuid=profile["id"])
    acc.set_tokens(mc["access_token"], ms_token.get("refresh_token", ""), mc.get("expires_in", 86400))
    return acc
