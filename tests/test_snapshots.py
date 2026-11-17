"""Снимки сборки: сохранить точку, история изменений, вернуть сборку, место на диске."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import snapshots as snap  # noqa: E402


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "inst"
        self.game = self.root / "minecraft"
        (self.game / "mods").mkdir(parents=True)
        (self.game / "config").mkdir()
        (self.game / "saves" / "World").mkdir(parents=True)
        self.write("mods/sodium-fabric-0.5.8+mc1.20.1.jar", b"SODIUM-OLD")
        self.write("mods/lithium-fabric-0.11.2.jar", b"LITHIUM")
        self.write("config/sodium-options.json", b'{"a":1}')
        self.write("options.txt", b"fov:70")
        self.write("saves/World/level.dat", b"WORLD")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, data):
        p = self.game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read(self, rel):
        return (self.game / rel).read_bytes()

    def test_same_state_does_not_create_new_snapshot(self):
        a = snap.create(self.root, self.game, "первый")
        b = snap.create(self.root, self.game, "второй")
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b["same"])
        self.assertEqual(len(snap.history(self.root)), 1)

    def test_history_shows_updated_added_removed_toggled_and_settings(self):
        snap.create(self.root, self.game, "до")
        (self.game / "mods/sodium-fabric-0.5.8+mc1.20.1.jar").unlink()
        self.write("mods/sodium-fabric-0.5.11+mc1.20.1.jar", b"SODIUM-NEW")
        self.write("mods/iris-1.7.0.jar", b"IRIS")
        (self.game / "mods/lithium-fabric-0.11.2.jar").rename(self.game / "mods/lithium-fabric-0.11.2.jar.disabled")
        self.write("config/sodium-options.json", b'{"a":2}')
        snap.create(self.root, self.game, "после")
        ch = snap.history(self.root)[0]["changes"]
        self.assertEqual(ch["updated"], [{"from": "sodium-fabric-0.5.8+mc1.20.1.jar", "to": "sodium-fabric-0.5.11+mc1.20.1.jar"}])
        self.assertEqual(ch["added"], ["iris-1.7.0.jar"])
        self.assertEqual(ch["removed"], [])
        self.assertEqual(ch["toggled"], [{"name": "lithium-fabric-0.11.2.jar", "on": False}])
        self.assertEqual(ch["settings"], 1)

    def test_restore_brings_back_files_and_keeps_worlds(self):
        first = snap.create(self.root, self.game, "до")
        (self.game / "mods/lithium-fabric-0.11.2.jar").unlink()
        self.write("mods/broken-1.0.jar", b"BROKEN")
        self.write("config/sodium-options.json", b'{"a":999}')
        self.write("config/new/deep.toml", b"x=1")
        self.write("saves/World/level.dat", b"WORLD-PLAYED")

        res = snap.restore(self.root, self.game, first["id"])

        self.assertEqual(self.read("mods/lithium-fabric-0.11.2.jar"), b"LITHIUM")
        self.assertFalse((self.game / "mods/broken-1.0.jar").exists())
        self.assertEqual(self.read("config/sodium-options.json"), b'{"a":1}')
        self.assertFalse((self.game / "config/new").exists(), "пустая папка от удалённого файла убрана")
        self.assertEqual(self.read("saves/World/level.dat"), b"WORLD-PLAYED", "миры откат не трогает")
        # откат можно отменить: состояние «до отката» сохранено
        snap.restore(self.root, self.game, res["before"])
        self.assertEqual(self.read("mods/broken-1.0.jar"), b"BROKEN")
        self.assertEqual(self.read("config/sodium-options.json"), b'{"a":999}')

    def test_identical_files_are_stored_once(self):
        for i in range(5):
            self.write("config/sodium-options.json", f'{{"a":{i}}}'.encode())
            snap.create(self.root, self.game, f"точка {i}")
        blobs = list((self.root / "snapshots" / "blobs").iterdir())
        # моды и options.txt — по одному разу, настройки — 5 версий
        self.assertEqual(len(blobs), 3 + 5)

    def test_old_snapshots_and_their_files_are_removed(self):
        for i in range(snap.KEEP + 3):
            self.write("config/sodium-options.json", f'{{"a":{i}}}'.encode())
            snap.create(self.root, self.game, f"точка {i}")
        self.assertEqual(len(snap.history(self.root)), snap.KEEP)
        blobs = list((self.root / "snapshots" / "blobs").iterdir())
        self.assertEqual(len(blobs), 3 + snap.KEEP)

    def test_restore_of_deleted_snapshot_fails_cleanly(self):
        with self.assertRaises(ValueError):
            snap.restore(self.root, self.game, "123")

    def test_pending_changes(self):
        snap.create(self.root, self.game, "до")
        self.write("mods/iris-1.7.0.jar", b"IRIS")
        self.assertEqual(snap.pending_changes(self.root, self.game)["added"], ["iris-1.7.0.jar"])


if __name__ == "__main__":
    unittest.main()
