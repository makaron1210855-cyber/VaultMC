"""«Лишние моды»: apply не должен удалять чужие файлы и выходить за папку mods (найдено в «полазь в коде» 8.0.17)."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core import optaudit


def jar(folder: Path, name: str, mod_id: str, content: bytes | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if content is not None:
        path.write_bytes(content)
        return path
    meta = {"schemaVersion": 1, "id": mod_id, "version": "1.0.0", "name": mod_id, "depends": {"minecraft": "1.21.1"}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))
    return path


class ApplySafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def test_existing_disabled_copy_is_never_deleted(self):
        # Пользователь уже выключал мод раньше: лежит старая копия «.jar.disabled». «Убрать» не должно её стереть.
        jar(self.mods, "cloth.jar", "foo")
        old = self.mods / "cloth.jar.disabled"
        old.write_bytes(b"old-copy")
        res = optaudit.apply(self.mods, "fabric", "1.21.1", ["cloth.jar"])
        self.assertEqual(old.read_bytes(), b"old-copy")
        self.assertTrue((self.mods / "cloth.jar").is_file(), "включённый мод не должен пропасть")
        self.assertEqual(res["disabled"], [])
        self.assertEqual([b["file"] for b in res["blocked"]], ["cloth.jar"])

    def test_names_outside_mods_folder_are_refused(self):
        outside = self.root / "outside.jar"
        outside.write_bytes(b"x")
        res = optaudit.apply(self.mods, "fabric", "1.21.1", ["../outside.jar"])
        self.assertTrue(outside.is_file(), "файл вне папки mods трогать нельзя")
        self.assertFalse((self.root / "outside.jar.disabled").exists())
        self.assertEqual(res["disabled"], [])

    def test_normal_apply_still_disables(self):
        jar(self.mods, "cloth.jar", "foo")
        res = optaudit.apply(self.mods, "fabric", "1.21.1", ["cloth.jar"])
        self.assertEqual(res["disabled"], ["cloth.jar"])
        self.assertTrue((self.mods / "cloth.jar.disabled").is_file())
        self.assertFalse((self.mods / "cloth.jar").exists())


class WorldSizeRaceTests(unittest.TestCase):
    def test_world_size_survives_file_removed_during_scan(self):
        from unittest import mock
        from core import worlds
        missing = Path(tempfile.mkdtemp()) / "gone.dat"          # файл исчез между обходом папки и stat
        with mock.patch.object(worlds, "world_files", return_value=iter([missing])):
            self.assertEqual(worlds._world_size(missing.parent), 0)


if __name__ == "__main__":
    unittest.main()
