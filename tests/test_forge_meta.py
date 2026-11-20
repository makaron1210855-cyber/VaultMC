"""Списки Forge/NeoForge: сбой одного сервера не должен стирать список другого загрузчика в кэше."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import forge

PROMOS = {"promos": {"1.20.1-recommended": "47.4.0", "1.20.1-latest": "47.4.28"}}
NEO = {"versions": ["21.1.1", "21.1.77"]}
LEGACY = {"versions": ["1.20.1-47.1.100", "1.20.1-47.1.106"]}


def fake_net(fail: set[str]):
    def get_json(url, timeout=15):
        if url in fail:
            raise OSError("нет ответа")
        return {forge.FORGE_PROMOS: PROMOS, forge.NEO_VERSIONS: NEO, forge.NEO_LEGACY_VERSIONS: LEGACY}[url]
    return get_json


class FetchMetaTests(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()) / "forge.json"

    def fetch(self, fail=()):
        with mock.patch.object(forge, "get_json", fake_net(set(fail))):
            return forge.fetch_meta(self.cache)

    def test_all_online(self):
        meta = self.fetch()
        self.assertEqual(meta["forge"]["1.20.1"][0], "47.4.0")
        self.assertEqual(meta["neoforge"]["1.21.1"], ["21.1.77", "21.1.1"])
        self.assertEqual(meta["neoforge"]["1.20.1"], ["47.1.106", "47.1.100"])

    def test_neoforge_down_keeps_cached_neoforge(self):
        self.fetch()
        meta = self.fetch(fail={forge.NEO_VERSIONS, forge.NEO_LEGACY_VERSIONS})
        self.assertIn("1.21.1", meta["neoforge"])
        self.assertIn("1.21.1", json.loads(self.cache.read_text(encoding="utf-8"))["neoforge"])

    def test_forge_down_keeps_cached_forge(self):
        self.fetch()
        meta = self.fetch(fail={forge.FORGE_PROMOS})
        self.assertIn("1.20.1", meta["forge"])

    def test_legacy_down_keeps_cached_1_20_1(self):
        self.fetch()
        meta = self.fetch(fail={forge.NEO_LEGACY_VERSIONS})
        self.assertEqual(meta["neoforge"]["1.20.1"], ["47.1.106", "47.1.100"])

    def test_everything_down_no_cache(self):
        meta = self.fetch(fail={forge.FORGE_PROMOS, forge.NEO_VERSIONS, forge.NEO_LEGACY_VERSIONS})
        self.assertEqual(meta, {"forge": {}, "neoforge": {}})
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
