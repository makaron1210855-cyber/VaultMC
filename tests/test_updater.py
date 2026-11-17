"""Автообновление: подпись version.json, контрольная сумма, пропуск версии после отката."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ed25519, updater  # noqa: E402

TEST_SECRET = bytes(range(1, 33))
TEST_PUBLIC = ed25519.public_key(TEST_SECRET).hex()


def manifest(version="99.0.0", **over):
    data = {"version": version, "url": "VaultMC-Setup.exe", "sha256": "ab" * 32, "notes": "n",
            "changes": {version: ["Новое"]}}
    data.update(over)
    data["sig"] = ed25519.sign(TEST_SECRET, updater.manifest_message(data)).hex()
    return data


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(updater, "UPDATE_PUBLIC_KEY", TEST_PUBLIC),
                        mock.patch.object(updater, "SKIP_FILE", Path(self._tmp.name) / "skip.txt")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    def check_with(self, data):
        with mock.patch.object(updater, "get_json", return_value=data):
            return updater.check()

    def test_signed_update_accepted(self):
        r = self.check_with(manifest())
        self.assertTrue(r["available"])

    def test_tampered_installer_hash_rejected(self):
        data = manifest()
        data["sha256"] = "cd" * 32                     # подменили установщик и его сумму на сайте
        with self.assertRaises(updater.UpdateError):
            self.check_with(data)

    def test_tampered_notes_rejected(self):
        data = manifest()
        data["changes"] = {"99.0.0": ["Скачайте лаунчер с evil.example"]}
        with self.assertRaises(updater.UpdateError):
            self.check_with(data)

    def test_missing_signature_rejected(self):
        data = manifest()
        del data["sig"]
        with self.assertRaises(updater.UpdateError):
            self.check_with(data)

    def test_missing_checksum_rejected(self):
        data = manifest(sha256="")
        with self.assertRaises(updater.UpdateError):
            self.check_with(data)

    def test_real_public_key_is_valid_point(self):
        self.assertEqual(len(bytes.fromhex(self.patches[0].temp_original)), 32)
        self.assertIsNotNone(ed25519._decompress(bytes.fromhex(self.patches[0].temp_original)))

    def test_same_version_needs_no_signature(self):
        with mock.patch.object(updater, "get_json", return_value={"version": updater.APP_VERSION}):
            self.assertFalse(updater.check()["available"])

    def test_rollback_skips_that_version(self):
        inst = Path(self._tmp.name) / "inst"
        inst.mkdir()
        (inst / "installed-by-setup.txt").write_text("version=99.0.0\r\ndesktop=0\r\n", encoding="utf-8")
        with mock.patch.object(updater, "_install_dir", return_value=inst):
            self.assertEqual(updater.note_rollback(), "99.0.0")
        self.assertFalse(self.check_with(manifest("99.0.0"))["available"], "с этой версии откатились — не предлагаем")
        self.assertTrue(self.check_with(manifest("99.0.1"))["available"], "более новую — предлагаем")

    def test_no_rollback_when_installed_matches(self):
        inst = Path(self._tmp.name) / "inst2"
        inst.mkdir()
        (inst / "installed-by-setup.txt").write_text(f"version={updater.APP_VERSION}\r\n", encoding="utf-8")
        with mock.patch.object(updater, "_install_dir", return_value=inst):
            self.assertEqual(updater.note_rollback(), "")

    def test_download_requires_checksum(self):
        with self.assertRaises(updater.UpdateError):
            fake = mock.MagicMock()
            fake.__enter__.return_value.headers = {}
            fake.__enter__.return_value.read.side_effect = [b"data", b""]
            with mock.patch.object(updater.urllib.request, "urlopen", return_value=fake), \
                 mock.patch.object(updater, "LAUNCHER_HOME", Path(self._tmp.name)):
                updater.download_installer({"version": "99.0.0", "url": "https://x.test/a.exe", "sha256": ""})


if __name__ == "__main__":
    unittest.main()
