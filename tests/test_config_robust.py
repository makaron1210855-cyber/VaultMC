"""config.json с неожиданным содержимым не должен ронять лаунчер."""
import json
import tempfile
import unittest
from pathlib import Path

from core.config import ConfigManager


class ConfigRobust(unittest.TestCase):
    def _load(self, text: str) -> ConfigManager:
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text(text, encoding="utf-8")
        return ConfigManager(d / "config.json")

    def test_list_instead_of_object(self):
        m = self._load("[]")
        self.assertEqual(m.config.download_threads, 32)
        self.assertTrue(list(m.path.parent.glob("config.broken-*.json")))

    def test_null_data_dir(self):
        self.assertTrue(self._load(json.dumps({"data_dir": None})).config is not None)

    def test_zero_threads_clamped(self):
        self.assertEqual(self._load(json.dumps({"download_threads": 0})).config.download_threads, 4)


if __name__ == "__main__":
    unittest.main()
