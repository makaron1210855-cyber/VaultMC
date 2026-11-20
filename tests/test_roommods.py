"""Моды комнаты: чем сборка гостя отличается от сборки хозяина (нет мода / другая версия) и замена версии."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import roommods  # noqa: E402
from core.modupdates import _sha1  # noqa: E402


def _jar(path: Path, mod_id: str, version: str) -> None:
    meta = {"schemaVersion": 1, "id": mod_id, "version": version, "name": mod_id}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))


class RoomModsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.host = self.root / "host" / "mods"
        self.guest = self.root / "guest" / "mods"
        self.host.mkdir(parents=True)
        self.guest.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _host_entry(self, jar: Path, mod_id: str, ver: str) -> dict:
        return {"id": mod_id, "name": mod_id, "ver": ver, "sha1": _sha1(jar), "size": jar.stat().st_size}

    def test_clean_list_keeps_safe_version_only(self):
        sha = "a" * 40
        out = roommods.clean_list([
            {"id": "a", "sha1": sha, "ver": "1.2.3+mc1.20.1"},
            {"id": "b", "sha1": sha, "ver": "<script>"},
            {"id": "c", "sha1": sha},
        ])
        self.assertEqual([m["ver"] for m in out], ["1.2.3+mc1.20.1", "", ""])

    def test_same_file_is_neither_missing_nor_changed(self):
        h = self.host / "a-1.0.jar"
        _jar(h, "a", "1.0")
        (self.guest / "a-1.0.jar").write_bytes(h.read_bytes())
        mods = [self._host_entry(h, "a", "1.0")]
        self.assertEqual(roommods.missing_mods(self.guest, mods), [])
        self.assertEqual(roommods.changed_mods(self.guest, mods), [])

    def test_missing_and_changed_are_told_apart(self):
        a, b = self.host / "a-2.0.jar", self.host / "b-1.0.jar"
        _jar(a, "a", "2.0")
        _jar(b, "b", "1.0")
        _jar(self.guest / "a-1.0.jar", "a", "1.0")        # у гостя старая версия a, мода b нет
        mods = [self._host_entry(a, "a", "2.0"), self._host_entry(b, "b", "1.0")]
        self.assertEqual([m["id"] for m in roommods.missing_mods(self.guest, mods)], ["b"])
        changed = roommods.changed_mods(self.guest, mods)
        self.assertEqual([m["id"] for m in changed], ["a"])
        self.assertEqual((changed[0]["have_ver"], changed[0]["have_file"]), ("1.0", "a-1.0.jar"))

    def test_update_mods_uses_own_file_name_and_reports_failures(self):
        a, c = self.host / "a-2.0.jar", self.host / "c-1.0.jar"
        _jar(a, "a", "2.0")
        _jar(c, "c", "1.0")
        _jar(self.guest / "a-1.0.jar", "a", "1.0")
        _jar(self.guest / "c-0.9.jar", "c", "0.9")
        mods = [self._host_entry(a, "a", "2.0"), self._host_entry(c, "c", "1.0")]
        found = {mods[0]["sha1"]: {"project_id": "PRJ", "version_number": "2.0", "files": [
            {"filename": "a-2.0.jar", "url": "https://cdn.modrinth.com/x/a-2.0.jar", "size": 1,
             "hashes": {"sha1": mods[0]["sha1"]}}]}}           # c на Modrinth не нашёлся
        captured = {}

        def fake_apply(game_dir, updates, log=print):
            captured["updates"] = updates
            return len(updates), []

        with mock.patch("core.net.post_json", return_value=found), mock.patch("core.modupdates.apply", fake_apply):
            res = roommods.update_mods(self.guest.parent, mods, lambda m: None)
        self.assertEqual(res, {"updated": ["a"], "failed": ["c"]})
        (u,) = captured["updates"]
        self.assertEqual((u["file"], u["new_file"], u["source"], u["project_id"]), ("a-1.0.jar", "a-2.0.jar", "modrinth", "PRJ"))

    def test_update_mods_refuses_foreign_url(self):
        a = self.host / "a-2.0.jar"
        _jar(a, "a", "2.0")
        _jar(self.guest / "a-1.0.jar", "a", "1.0")
        mods = [self._host_entry(a, "a", "2.0")]
        found = {mods[0]["sha1"]: {"project_id": "PRJ", "files": [
            {"filename": "a-2.0.jar", "url": "https://evil.example/a.jar", "hashes": {"sha1": mods[0]["sha1"]}}]}}
        with mock.patch("core.net.post_json", return_value=found), mock.patch("core.modupdates.apply") as ap:
            res = roommods.update_mods(self.guest.parent, mods, lambda m: None)
        self.assertEqual(res, {"updated": [], "failed": ["a"]})
        ap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
