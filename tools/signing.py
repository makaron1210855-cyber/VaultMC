"""Ключ подписи обновлений VaultMC (Ed25519) — только на ПК владельца, в репозиторий и на сайт не попадает.

Файл: %USERPROFILE%\\.vaultmc-release\\update-signing.key (32 байта в hex). СОХРАНИТЕ ЕГО КОПИЮ (флешка, облако):
без него новые выпуски нельзя подписать, и игрокам придётся один раз переустановить лаунчер с сайта вручную.
Открытая половина ключа вшита в core/updater.py (UPDATE_PUBLIC_KEY).
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ed25519  # noqa: E402
from core.updater import UPDATE_PUBLIC_KEY, manifest_message  # noqa: E402

KEY_FILE = Path.home() / ".vaultmc-release" / "update-signing.key"


def load_key(create: bool = False) -> bytes:
    if KEY_FILE.exists():
        return bytes.fromhex(KEY_FILE.read_text(encoding="ascii").strip())
    if not create:
        raise SystemExit(f"Нет ключа подписи обновлений: {KEY_FILE}\n"
                         "Верните файл из резервной копии. Без него выпуск нельзя подписать.")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    KEY_FILE.write_text(secret.hex(), encoding="ascii")
    return secret


def sign_manifest(info: dict) -> dict:
    """Подписывает version.json. Проверяет, что ключ тот, чья открытая половина вшита в лаунчер."""
    secret = load_key()
    public = ed25519.public_key(secret).hex()
    if public != UPDATE_PUBLIC_KEY:
        raise SystemExit("Ключ подписи не совпадает с UPDATE_PUBLIC_KEY в core/updater.py — подпись не примут лаунчеры.\n"
                         f"Ключ из файла даёт {public}")
    signed = {**info, "sig": ed25519.sign(secret, manifest_message(info)).hex()}
    if not ed25519.verify(bytes.fromhex(UPDATE_PUBLIC_KEY), manifest_message(signed), bytes.fromhex(signed["sig"])):
        raise SystemExit("Подпись не прошла проверку — выпуск остановлен")
    return signed


if __name__ == "__main__":
    # py tools/signing.py --create — один раз: создать ключ и показать открытую половину для core/updater.py
    key = load_key(create="--create" in sys.argv)
    print("Файл ключа:", KEY_FILE)
    print("Открытый ключ (UPDATE_PUBLIC_KEY):", ed25519.public_key(key).hex())
