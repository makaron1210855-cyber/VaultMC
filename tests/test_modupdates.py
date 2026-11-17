"""Откат модов: apply кладёт старый .jar в архив, rollback возвращает его обратно."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import modupdates as mu  # noqa: E402


def _make_game(tmp: Path) -> Path:
    game = tmp / "game"
    (game / "mods").mkdir(parents=True)
    return game


def _update(old_name: str, new_name: str, **extra) -> dict:
    base = {
        "file": old_name,
        "new_file": new_name,
        "url": "https://example.invalid/mod.jar",
        "sha1": None,
        "size": None,
        "title": "Test Mod",
        "version": "2.0.0",
        "source": "modrinth",
        "project_id": "abc123",
    }
    base.update(extra)
    return base


class ApplyAndRollbackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.game = _make_game(self.tmp)
        self.mods = self.game / "mods"
        (self.mods / "testmod-1.0.jar").write_bytes(b"OLD")

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_download(self, payload: bytes):
        def _dl(url, dest, sha1=None, size=None, *a, **kw):
            Path(dest).write_bytes(payload)
            return Path(dest)
        return mock.patch.object(mu, "download", side_effect=_dl)

    def test_apply_replaces_jar_and_keeps_old_in_backup(self):
        with self._fake_download(b"NEW"):
            done, errors = mu.apply(self.game, [_update("testmod-1.0.jar", "testmod-2.0.jar")], log=lambda *_: None)
        self.assertEqual(done, 1)
        self.assertEqual(errors, [])
        self.assertFalse((self.mods / "testmod-1.0.jar").exists())
        self.assertEqual((self.mods / "testmod-2.0.jar").read_bytes(), b"NEW")
        backups = mu.list_backups(self.game)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["title"], "Test Mod")

    def test_rollback_restores_old_jar_and_removes_new(self):
        with self._fake_download(b"NEW"):
            mu.apply(self.game, [_update("testmod-1.0.jar", "testmod-2.0.jar")], log=lambda *_: None)
        backup_id = mu.list_backups(self.game)[0]["id"]

        title = mu.rollback(self.game, backup_id)

        self.assertEqual(title, "Test Mod")
        self.assertEqual((self.mods / "testmod-1.0.jar").read_bytes(), b"OLD")
        self.assertFalse((self.mods / "testmod-2.0.jar").exists())
        self.assertEqual(mu.list_backups(self.game), [])

    def test_rollback_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            mu.rollback(self.game, "no-such-id")

    def test_rollback_when_backup_file_missing_raises_and_keeps_mods(self):
        with self._fake_download(b"NEW"):
            mu.apply(self.game, [_update("testmod-1.0.jar", "testmod-2.0.jar")], log=lambda *_: None)
        backup_id = mu.list_backups(self.game)[0]["id"]
        for f in (self.game / mu.BACKUP_DIR).glob("*.jar"):
            f.unlink()

        with self.assertRaises(ValueError):
            mu.rollback(self.game, backup_id)
        self.assertEqual((self.mods / "testmod-2.0.jar").read_bytes(), b"NEW")

    def test_failed_download_keeps_old_jar_and_reports_error(self):
        with mock.patch.object(mu, "download", side_effect=OSError("сеть недоступна")):
            done, errors = mu.apply(self.game, [_update("testmod-1.0.jar", "testmod-2.0.jar")], log=lambda *_: None)
        self.assertEqual(done, 0)
        self.assertEqual(len(errors), 1)
        self.assertEqual((self.mods / "testmod-1.0.jar").read_bytes(), b"OLD")
        self.assertFalse((self.mods / "testmod-2.0.jar.vaultnew").exists())
        self.assertEqual(mu.list_backups(self.game), [])

    def test_disabled_mod_stays_disabled_after_update(self):
        (self.mods / "testmod-1.0.jar").rename(self.mods / "testmod-1.0.jar.disabled")
        with self._fake_download(b"NEW"):
            done, _ = mu.apply(
                self.game,
                [_update("testmod-1.0.jar.disabled", "testmod-2.0.jar")],
                log=lambda *_: None,
            )
        self.assertEqual(done, 1)
        self.assertTrue((self.mods / "testmod-2.0.jar.disabled").exists())

    def test_archive_keeps_only_last_backup_keep_entries(self):
        entries = [{"id": str(i), "backup": f"{i}_x.jar", "title": "t"} for i in range(mu.BACKUP_KEEP + 5)]
        folder = mu._backup_dir(self.game)
        folder.mkdir(parents=True, exist_ok=True)
        for e in entries:
            (folder / e["backup"]).write_bytes(b"x")

        mu._write_manifest(self.game, entries)

        kept = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(kept), mu.BACKUP_KEEP)
        self.assertEqual(kept[-1]["id"], str(mu.BACKUP_KEEP + 4))
        self.assertFalse((folder / "0_x.jar").exists())


class VersionRangeTests(unittest.TestCase):
    """Подходит ли мод под версию игры — от этого зависит предупреждение в модчеке."""

    def test_fabric_caret_and_ranges(self):
        from core.modcheck import version_matches
        self.assertTrue(version_matches("1.20.1", ">=1.20 <1.21"))
        self.assertFalse(version_matches("1.21", ">=1.20 <1.21"))
        self.assertTrue(version_matches("1.20.1", "~1.20.1"))
        self.assertTrue(version_matches("1.20.4", "1.20.x"))
        self.assertFalse(version_matches("1.19.4", "1.20.x"))

    def test_forge_maven_ranges(self):
        from core.modcheck import version_matches
        self.assertTrue(version_matches("1.20.1", "[1.20,1.21)"))
        self.assertFalse(version_matches("1.21", "[1.20,1.21)"))
        self.assertTrue(version_matches("1.20.1", "[1.20.1]"))

    def test_any_and_unknown(self):
        from core.modcheck import version_matches
        self.assertTrue(version_matches("1.20.1", "*"))
        self.assertTrue(version_matches("1.20.1", ""))
        self.assertIsNone(version_matches("1.20.1", ">= ?? 1.2"))   # непонятная запись — не судим
        self.assertIsNone(version_matches("1.20.1", "1.20.1-snapshot"))
        self.assertIsNone(version_matches("1.21", ">=1.21-pre1"))
        self.assertTrue(version_matches("1.20.1", ">=1.20-"), "дефис без букв — обычная запись Fabric")


if __name__ == "__main__":
    unittest.main()
