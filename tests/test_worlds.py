"""Миры: автокопии после игры, корзина удалённых миров."""
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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

    def add_dh_cache(self, name, size=5000):
        """Кэш мода Distant Horizons, как в настоящем мире: data/DistantHorizons.sqlite (+ -wal, -shm)."""
        data = self.game / "saves" / name / "data"
        data.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            (data / f"DistantHorizons.sqlite{suffix}").write_bytes(b"D" * size)
        (data / "raids.dat").write_bytes(b"RAIDS")

    def test_backup_skips_distant_horizons_cache(self):
        # раньше кэш (у игроков 8–26 ГБ) целиком попадал в копию мира
        self.add_dh_cache("Alpha")
        (self.game / "saves" / "Alpha" / "session.lock").write_bytes(b"\xe2\x98\x83")
        path = W.backup_world(self.game, "Alpha", self.backups)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        self.assertIn("Alpha/data/raids.dat", names, "остальные файлы data/ — часть мира, они в копии")
        self.assertIn("Alpha/region/r.0.0.mca", names)
        self.assertEqual([n for n in names if "DistantHorizons" in n], [], "кэш Distant Horizons в копию не попадает")
        self.assertNotIn("Alpha/session.lock", names)

    def test_list_shows_world_size_without_cache(self):
        # в списке миров было 10,4 ГБ при настоящем мире в 0,5 ГБ: считался и кэш Distant Horizons
        self.add_dh_cache("Alpha", size=200_000)
        a = next(w for w in W.list_worlds(self.game) if w["folder"] == "Alpha")
        self.assertLess(a["size"], 10_000)
        self.assertEqual(a["cache"], 600_000)
        b = next(w for w in W.list_worlds(self.game) if w["folder"] == "Beta")
        self.assertEqual(b["cache"], 0)

    def test_auto_backup_not_blocked_by_cache(self):
        # раньше мир с кэшем Distant Horizons считался «больше 2 ГБ», и автокопия не делалась вовсе
        self.add_dh_cache("Alpha", size=4000)            # кэш 12 000 байт, сам мир ~1 000
        with mock.patch.object(W, "AUTO_MAX_BYTES", 5000):
            self.assertIn("Alpha", W.auto_backup_changed(self.game, self.backups, log=lambda m: None))
            # мир, который сам больше предела, по-прежнему не копируется автоматически
            (self.game / "saves" / "Beta" / "region" / "r.1.0.mca").write_bytes(b"R" * 6000)
            self.touch_level("Beta")
            self.assertNotIn("Beta", W.auto_backup_changed(self.game, self.backups, log=lambda m: None))

    def test_all_worlds_backup_skips_cache_and_restores_worlds_apart(self):
        # «Копия всех миров» сборки: раньше в ней был кэш Distant Horizons, а «Восстановить» сваливало
        # все миры в одну папку (одинаковые level.dat и регионы затирали друг друга)
        from core import instances as I
        self.add_dh_cache("Alpha")
        with mock.patch.object(I, "INSTANCES_DIR", self.root / "instances"):
            inst = I.Instance(id="t", name="t", version_id="1.20.1", game_version="1.20.1")
            shutil.copytree(self.game / "saves", inst.game_dir / "saves")
            archive = I.InstanceManager(self.root / "instances").backup_worlds(inst)
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
            self.assertIn("Alpha/level.dat", names)
            self.assertIn("Beta/level.dat", names)
            self.assertEqual([n for n in names if "DistantHorizons" in n], [])
            self.assertFalse(any((inst.root / "backups").glob("*.part")), "временных файлов не остаётся")
            restored = W.restore_backup(inst.game_dir, archive.parent, archive.name)
            self.assertEqual(restored, "Alpha (2), Beta (2)")
            saves = inst.game_dir / "saves"
            self.assertEqual((saves / "Alpha (2)" / "level.dat").read_bytes(), b"LEVEL-Alpha")
            self.assertEqual((saves / "Beta (2)" / "level.dat").read_bytes(), b"LEVEL-Beta")
            self.assertEqual((saves / "Alpha" / "level.dat").read_bytes(), b"LEVEL-Alpha", "текущие миры не тронуты")

    def test_restore_single_world_backup_as_before(self):
        path = W.backup_world(self.game, "Alpha", self.backups)
        self.assertEqual(W.restore_backup(self.game, self.backups, path.name), "Alpha (2)")
        self.assertEqual((self.game / "saves" / "Alpha (2)" / "region" / "r.0.0.mca").read_bytes(), b"R" * 1000)

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

    def test_parallel_passes_copy_once(self):
        """Игру быстро запустили и закрыли три раза: проходы копирования не должны копировать миры трижды."""
        import threading
        gate = threading.Event()
        real = W.backup_world

        def slow(*a, **kw):
            gate.wait(5)
            return real(*a, **kw)
        results = []
        with mock.patch.object(W, "backup_world", side_effect=slow):
            threads = [threading.Thread(target=lambda: results.append(
                W.auto_backup_changed(self.game, self.backups, log=lambda m: None))) for _ in range(3)]
            for t in threads:
                t.start()
            time.sleep(0.3)
            gate.set()
            for t in threads:
                t.join()
        self.assertEqual(sorted(sum(results, [])), ["Alpha", "Beta"])
        self.assertEqual(len(list(self.backups.glob("*.zip"))), 2)

    def test_interrupted_pass_keeps_done_worlds(self):
        calls = []

        def ok():
            calls.append(1)
            return len(calls) < 2          # после первого мира игру снова запустили
        self.assertEqual(len(W.auto_backup_changed(self.game, self.backups, still_ok=ok, log=lambda m: None)), 1)
        self.assertEqual(len(W.auto_backup_changed(self.game, self.backups, log=lambda m: None)), 1,
                         "второй проход копирует только оставшийся мир")

    def test_stale_part_removed_fresh_kept(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        old, fresh = self.backups / "Alpha auto 1.part", self.backups / "Alpha auto 2.part"
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")
        t = time.time() - W.PART_MAX_AGE - 10
        os.utime(old, (t, t))
        self.assertEqual(W.clean_stale_parts(self.backups), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_dedupe_keeps_newest_and_manual(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        for name in ("Alpha auto 2026-11-18 10-25-43.zip", "Alpha auto 2026-11-18 10-25-47.zip",
                     "Alpha auto 2026-11-18 10-25-53.zip", "Alpha 2026-11-18 10-25-50.zip"):
            (self.backups / name).write_bytes(b"same")
        (self.backups / "Alpha auto 2026-11-19 10-00-00.zip").write_bytes(b"diff")   # тот же размер, другое содержимое
        self.assertEqual(W.dedupe_auto_backups(self.backups), 2)
        left = sorted(f.name for f in self.backups.glob("*.zip"))
        self.assertEqual(left, ["Alpha 2026-11-18 10-25-50.zip", "Alpha auto 2026-11-18 10-25-53.zip",
                                "Alpha auto 2026-11-19 10-00-00.zip"])

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

    def test_two_manual_backups_in_same_second_keep_both(self):
        # раньше вторая копия в ту же секунду перезаписывала первую (имя файла — только до секунды)
        first = W.backup_world(self.game, "Alpha", self.backups)
        (self.game / "saves" / "Alpha" / "level.dat").write_bytes(b"CHANGED")
        second = W.backup_world(self.game, "Alpha", self.backups)
        self.assertNotEqual(first, second)
        self.assertEqual(len(list(self.backups.glob("*.zip"))), 2)

    def test_two_trashed_worlds_in_same_second_stay_separate(self):
        # раньше shutil.move клал второй мир внутрь первого (та же секунда и то же имя в корзине)
        first = W.trash_world(self.game, "Alpha", self.trash)
        (self.game / "saves" / "Alpha").mkdir()
        (self.game / "saves" / "Alpha" / "level.dat").write_bytes(b"SECOND")
        second = W.trash_world(self.game, "Alpha", self.trash)
        self.assertNotEqual(first, second)
        self.assertEqual(len(W.list_trash(self.trash)), 2)
        self.assertFalse((self.trash / first / "Alpha").exists())


if __name__ == "__main__":
    unittest.main()
