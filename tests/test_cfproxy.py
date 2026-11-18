"""Каталог CurseForge без ключа у игрока: запросы идут через сервер VaultMC (server/worker.js, /api/cf/...)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import keys  # noqa: E402
from core import modsources as ms  # noqa: E402


class CfEndpointTest(unittest.TestCase):
    def test_own_key_goes_direct(self):
        base, hdr = ms.cf_endpoint("my-key")
        self.assertEqual(base, ms.CURSEFORGE_API)
        self.assertEqual(hdr, {"x-api-key": "my-key"})

    def test_no_key_goes_via_server_without_key_header(self):
        with mock.patch.object(keys, "SKIN_SERVER", "https://vaultmc-skins.example/"):
            base, hdr = ms.cf_endpoint(ms.CF_VIA_SERVER)
        self.assertEqual(base, "https://vaultmc-skins.example/api/cf")
        self.assertEqual(hdr, {})

    def test_empty_key_also_goes_via_server(self):
        with mock.patch.object(keys, "SKIN_SERVER", "https://vaultmc-skins.example"):
            base, _ = ms.cf_endpoint("")
        self.assertEqual(base, "https://vaultmc-skins.example/api/cf")

    def test_no_server_and_no_key_is_an_error(self):
        with mock.patch.object(keys, "SKIN_SERVER", ""):
            with self.assertRaises(ms.ModSourceError):
                ms.cf_endpoint(ms.CF_VIA_SERVER)


if __name__ == "__main__":
    unittest.main()
