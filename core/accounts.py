"""
Аккаунты (профили) трёх типов — можно держать несколько и переключаться:

  offline   — локальный аккаунт лаунчера (ник + пароль, PBKDF2). Лицензия не нужна.
  elyby     — аккаунт Ely.by: скины и вход на серверы с Ely.by. Лицензия не нужна.
  microsoft — лицензия Minecraft (необязательно).

Токены Ely.by/Microsoft на Windows шифруются DPAPI (utils.protect).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .utils import LAUNCHER_HOME, NICKNAME_RE, offline_uuid, protect, unprotect

PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD = 6
KIND_LABELS = {"offline": "Оффлайн", "elyby": "Ely.by", "microsoft": "Microsoft"}


class AuthError(Exception):
    """Понятное пользователю сообщение."""


@dataclass
class Account:
    id: str
    kind: str                      # offline | elyby | microsoft
    username: str
    uuid: str
    access_token: str = ""         # хранится зашифрованным (см. token())
    refresh_token: str = ""
    client_token: str = ""
    expires_at: float = 0.0
    password_hash: str = ""        # только offline
    salt: str = ""
    created_at: float = field(default_factory=time.time)
    last_login: float = 0.0
    play_seconds: int = 0
    launches: int = 0
    last_instance: str = ""
    remembered: bool = False       # offline: пароль уже вводили на этом ПК — переключаться без пароля

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def token(self) -> str:
        return unprotect(self.access_token)

    def refresh(self) -> str:
        return unprotect(self.refresh_token)

    def set_tokens(self, access: str, refresh: str = "", expires_in: float = 0) -> None:
        self.access_token = protect(access)
        if refresh:
            self.refresh_token = protect(refresh)
        self.expires_at = time.time() + expires_in if expires_in else 0.0


def password_strength(password: str) -> int:
    """0..4 для индикатора надёжности."""
    if len(password) < MIN_PASSWORD:
        return 0
    score = 0
    score += len(password) >= 8
    score += any(c.isdigit() for c in password)
    score += any(c.islower() for c in password) and any(c.isupper() for c in password)
    score += any(not c.isalnum() for c in password)
    return score


class AccountStore:
    def __init__(self, path: Path = LAUNCHER_HOME / "accounts.json"):
        self.path = path
        self.accounts: dict[str, Account] = {}
        self.active_id = ""
        self._load()

    # ---------------------------------------------------------------- хранение
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # файл повреждён: откладываем копию, чтобы следующее сохранение не стёрло аккаунты безвозвратно
            try:
                self.path.replace(self.path.with_name(f"accounts.broken-{int(time.time())}.json"))
            except OSError:
                pass
            return
        if "accounts" not in raw:  # формат v2: {ник: {...}} — переносим как оффлайн-аккаунты
            for data in raw.values():
                acc = Account(id=secrets.token_hex(6), kind="offline", username=data["username"],
                              uuid=offline_uuid(data["username"]), password_hash=data["password_hash"],
                              salt=data["salt"], created_at=data.get("created_at", time.time()),
                              play_seconds=data.get("play_seconds", 0), launches=data.get("launches", 0))
                self.accounts[acc.id] = acc
            self.save()
            return
        known = Account.__dataclass_fields__
        for data in raw["accounts"]:
            acc = Account(**{k: v for k, v in data.items() if k in known})
            self.accounts[acc.id] = acc
        self.active_id = raw.get("active", "")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"active": self.active_id, "accounts": [asdict(a) for a in self.accounts.values()]},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- общее
    @property
    def active(self) -> Account | None:
        return self.accounts.get(self.active_id)

    def set_active(self, account_id: str) -> None:
        self.active_id = account_id
        acc = self.accounts.get(account_id)
        if acc:
            acc.last_login = time.time()
        self.save()

    def list(self) -> list[Account]:
        return sorted(self.accounts.values(), key=lambda a: a.last_login, reverse=True)

    def remove(self, account_id: str) -> None:
        self.accounts.pop(account_id, None)
        if self.active_id == account_id:
            self.active_id = ""
        self.save()

    def add_or_update(self, account: Account) -> Account:
        """Тот же тип + тот же UUID = тот же аккаунт (обновляем токены, статистику сохраняем)."""
        for existing in self.accounts.values():
            if existing.kind == account.kind and existing.uuid == account.uuid:
                existing.username = account.username
                existing.access_token, existing.refresh_token = account.access_token, account.refresh_token
                existing.client_token, existing.expires_at = account.client_token, account.expires_at
                self.set_active(existing.id)
                return existing
        self.accounts[account.id] = account
        self.set_active(account.id)
        return account

    def record_session(self, account_id: str, seconds: int, instance_name: str) -> None:
        acc = self.accounts.get(account_id)
        if acc:
            acc.play_seconds += max(0, seconds)
            acc.launches += 1
            acc.last_instance = instance_name
            self.save()

    # ---------------------------------------------------------------- оффлайн
    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()

    @staticmethod
    def _check_password(password: str, repeat: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise AuthError(f"Пароль должен быть не короче {MIN_PASSWORD} символов")
        if password != repeat:
            raise AuthError("Пароли не совпадают")

    def _find_offline(self, username: str) -> Account | None:
        return next((a for a in self.accounts.values()
                     if a.kind == "offline" and a.username.lower() == username.strip().lower()), None)

    def register_offline(self, username: str, password: str, repeat: str) -> Account:
        username = username.strip()
        if not NICKNAME_RE.match(username):
            raise AuthError("Ник: 3–16 символов, латиница, цифры и _")
        if self._find_offline(username):
            raise AuthError("Оффлайн-аккаунт с таким ником уже есть — войдите")
        self._check_password(password, repeat)
        salt = secrets.token_hex(16)
        acc = Account(id=secrets.token_hex(6), kind="offline", username=username, uuid=offline_uuid(username),
                      password_hash=self._hash(password, salt), salt=salt)
        self.accounts[acc.id] = acc
        acc.remembered = True
        self.set_active(acc.id)
        return acc

    def login_offline(self, username: str, password: str) -> Account:
        acc = self._find_offline(username)
        if acc is None:
            raise AuthError("Аккаунт не найден — зарегистрируйтесь")
        if not hmac.compare_digest(self._hash(password, acc.salt), acc.password_hash):
            raise AuthError("Неверный пароль")
        acc.remembered = True
        self.set_active(acc.id)
        return acc

    def change_password(self, account_id: str, old: str, new: str, repeat: str) -> None:
        acc = self.accounts[account_id]
        if acc.kind != "offline":
            raise AuthError("Пароль меняется на сайте сервиса")
        if not hmac.compare_digest(self._hash(old, acc.salt), acc.password_hash):
            raise AuthError("Неверный текущий пароль")
        self._check_password(new, repeat)
        acc.salt = secrets.token_hex(16)
        acc.password_hash = self._hash(new, acc.salt)
        self.save()
