"""Модчек: проверка модов до запуска — не тот загрузчик, не та версия, дубликаты."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import modcheck  # noqa: E402


def _fabric_jar(path: Path, mod_id: str, minecraft: str = "*", depends: dict | None = None) -> None:
    meta = {
        "schemaVersion": 1,
        "id": mod_id,
        "version": "1.0.0",
        "name": mod_id,
        "depends": {"minecraft": minecraft, **(depends or {})},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))


class CheckModsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name) / "mods"
        self.mods.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_mod_for_newer_game_is_reported(self):
        _fabric_jar(self.mods / "newmod.jar", "newmod", minecraft=">=1.21")
        problems = modcheck.check_mods(self.mods, "fabric", "1.20.1")
        self.assertTrue(problems, "мод под 1.21 в игре 1.20.1 должен попасть в проблемы")
        self.assertTrue(any("newmod" in (p.get("file", "") + p.get("title", "")) for p in problems))

    def test_matching_mod_is_not_reported(self):
        _fabric_jar(self.mods / "okmod.jar", "okmod", minecraft=">=1.20 <1.21")
        problems = modcheck.check_mods(self.mods, "fabric", "1.20.1")
        self.assertEqual([p for p in problems if "okmod" in p.get("file", "")], [])

    def test_empty_mods_folder_gives_no_problems(self):
        self.assertEqual(modcheck.check_mods(self.mods, "fabric", "1.20.1"), [])

    def test_problem_has_required_keys(self):
        _fabric_jar(self.mods / "newmod.jar", "newmod", minecraft=">=1.21")
        for p in modcheck.check_mods(self.mods, "fabric", "1.20.1"):
            for key in ("id", "title", "text", "file", "fix"):
                self.assertIn(key, p)


if __name__ == "__main__":
    unittest.main()
