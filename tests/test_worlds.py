"""Миры: автокопии после игры, корзина удалённых миров."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import worlds as W  # noqa: E402


class WorldTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = self.root / "minecraft"
        self.backups = self.root / "backups"
        self.trash = self.root / "trash"
        for name in ("Alpha", "Beta"):
            d = self.game / "saves" / name
            (d / "region").mkdir(parents=True)
            (d / "level.dat").write_bytes(b"LEVEL-" + name.encode())
            (d / "region" / "r.0.0.mca").write_bytes(b"R" * 1000)

    def tearDown(self):
        self._tmp.cleanup()

    def touch_level(self, name, ahead=10):
        p = self.game / "saves" / name / "level.dat"
        t = time.time() + ahead
        os.utime(p, (t, t))

    def test_auto_backup_only_changed_worlds(self):
        self.assertEqual(sorted(W.auto_backup_changed(self.game, self.backups, log=lambda m: None)), ["Alpha", "Beta"])
        self.assertEqual(W.auto_backup_changed(self.game, self.backups, log=lambda m: None), [], "ничего не играли — копий нет")
        self.touch_level("Beta")
        self.assertEqual(W.auto_backup_changed(self.game, self.backups, log=lambda m: None), ["Beta"])
        items = W.list_backups(self.backups)
        self.assertTrue(all(b["auto"] for b in items))
        self.assertFalse(any(self.backups.glob("*.part")), "временных файлов не остаётся")

    def test_auto_backups_limited_and_manual_untouched(self):
        W.backup_world(self.game, "Alpha", self.backups)                  # ручная
        for i in range(W.AUTO_PER_WORLD + 3):
            W.backup_world(self.game, "Alpha", self.backups, auto=True)
            newest = max(self.backups.glob("*.zip"), key=lambda f: f.stat().st_mtime)
            t = time.time() + i
            os.utime(newest, (t, t))
            time.sleep(1.01)                                             # в имени копии — секунды
        items = W.list_backups(self.backups)
        self.assertEqual(sum(b["auto"] for b in items), W.AUTO_PER_WORLD)
        self.assertEqual(sum(not b["auto"] for b in items), 1, "ручная копия не удаляется автокопиями")

    def test_stops_when_game_started_again(self):
        self.assertEqual(W.auto_backup_changed(self.game, self.backups, still_ok=lambda: False, log=lambda m: None), [])

    def test_trash_restore_and_purge(self):
        entry = W.trash_world(self.game, "Alpha", self.trash)
        self.assertFalse((self.game / "saves" / "Alpha").exists())
        listed = W.list_trash(self.trash)
        self.assertEqual([t["name"] for t in listed], ["Alpha"])
        self.assertEqual(listed[0]["left_days"], W.TRASH_DAYS)
        back = W.restore_trash(self.game, self.trash, entry)
        self.assertEqual(back, "Alpha")
        self.assertEqual((self.game / "saves" / "Alpha" / "level.dat").read_bytes(), b"LEVEL-Alpha")

        W.trash_world(self.game, "Beta", self.trash)
        W.purge_trash(self.trash, days=0)
        self.assertEqual(W.list_trash(self.trash), [])

    def test_restore_from_trash_does_not_overwrite_new_world_with_same_name(self):
        entry = W.trash_world(self.game, "Alpha", self.trash)
        (self.game / "saves" / "Alpha").mkdir()
        (self.game / "saves" / "Alpha" / "level.dat").write_bytes(b"NEW")
        self.assertEqual(W.restore_trash(self.game, self.trash, entry), "Alpha (2)")
        self.assertEqual((self.game / "saves" / "Alpha" / "level.dat").read_bytes(), b"NEW")


if __name__ == "__main__":
    unittest.main()
