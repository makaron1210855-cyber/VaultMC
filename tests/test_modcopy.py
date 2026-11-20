"""Моды из другой сборки (core/modcopy.py): что переносится, зависимости и защита от конфликтов."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core import modcopy


def jar(folder: Path, name: str, mod_id: str, depends: dict | None = None, breaks: dict | None = None,
        mc: str = "1.20.1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    meta = {"schemaVersion": 1, "id": mod_id, "version": "1.0.0", "name": mod_id.title(),
            "depends": {"minecraft": mc, **(depends or {})}, "breaks": breaks or {}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))
    return path


class ModCopyTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.src, self.dst = root / "src", root / "dst"
        self.src_mods, self.dst_mods = self.src / "mods", self.dst / "mods"
        jar(self.dst_mods, "sodium-0.5.jar", "sodium")
        jar(self.dst_mods, "lithium-0.11.jar", "lithium")

    def plan(self):
        return modcopy.plan(self.src, "fabric", "1.20.1", self.dst, "fabric", "1.20.1", log=lambda m: None)

    def install(self, files):
        items = self.plan()["items"]
        return modcopy.install(self.src, self.dst, "fabric", "1.20.1", items, files, log=lambda m: None)

    def names(self):
        return sorted(f.name for f in self.dst_mods.iterdir() if f.suffix == ".jar")

    def test_plan_skips_present_and_service_mods(self):
        jar(self.src_mods, "sodium-0.6.jar", "sodium")
        jar(self.src_mods, "e4mc-5.0.jar", "e4mc_minecraft")
        jar(self.src_mods, "ferritecore-6.jar", "ferritecore")
        p = self.plan()
        self.assertTrue(p["same"])
        self.assertEqual(p["already"], 1)
        self.assertEqual([i["file"] for i in p["items"]], ["ferritecore-6.jar"])
        self.assertEqual(p["items"][0]["status"], "copy")

    def test_install_copies_and_adds_dependency(self):
        jar(self.src_mods, "fabric-api-0.92.jar", "fabric-api")
        jar(self.src_mods, "modmenu-7.jar", "modmenu", depends={"fabric-api": "*"})
        r = self.install(["modmenu-7.jar"])
        self.assertEqual(sorted(r["installed"]), ["Fabric-Api", "Modmenu"])
        self.assertIn("fabric-api-0.92.jar", self.names())
        self.assertIn("modmenu-7.jar", self.names())
        self.assertFalse((self.dst / modcopy.STAGE_DIR).exists())

    def test_missing_dependency_not_installed(self):
        jar(self.src_mods, "needs-1.jar", "needs", depends={"nowhere": "*"})
        r = self.install(["needs-1.jar"])
        self.assertEqual(r["installed"], [])
        self.assertIn("nowhere", r["skipped"][0]["reason"])
        self.assertNotIn("needs-1.jar", self.names())

    def test_breaks_existing_mod_not_installed_and_existing_kept(self):
        jar(self.src_mods, "bad-1.jar", "bad", breaks={"lithium": "*"})
        jar(self.src_mods, "good-1.jar", "good")
        r = self.install(["bad-1.jar", "good-1.jar"])
        self.assertEqual(r["installed"], ["Good"])
        self.assertIn("lithium-0.11.jar", self.names())
        self.assertNotIn("bad-1.jar", self.names())

    def test_known_conflict_optifine_with_sodium(self):
        jar(self.src_mods, "OptiFine_1.20.1.jar", "optifabric")
        r = self.install(["OptiFine_1.20.1.jar"])
        self.assertEqual(r["installed"], [])
        self.assertIn("OptiFine", r["skipped"][0]["reason"])
        self.assertIn("sodium-0.5.jar", self.names())

    def test_wrong_game_version_not_on_modrinth_is_not_offered(self):
        jar(self.src_mods, "old-1.jar", "old", mc="1.16.5")
        with mock.patch.object(modcopy, "post_json", return_value={}):
            item = self.plan()["items"][0]
        self.assertEqual(item["status"], "no")
        self.assertIn("1.20.1", item["reason"])

    def test_other_version_found_on_modrinth_is_downloaded(self):
        path = jar(self.src_mods, "old-1.jar", "old", mc="1.16.5")
        sha = modcopy._sha1(path)
        found = {sha: {"project_id": "P1", "version_number": "2.0", "files": [
            {"primary": True, "url": "https://cdn.modrinth.com/x/old-2.jar", "filename": "old-2.jar",
             "hashes": {"sha1": "ab" * 20}, "size": 10}]}}
        with mock.patch.object(modcopy, "post_json", return_value=found):
            item = self.plan()["items"][0]
        self.assertEqual(item["status"], "download")
        self.assertEqual(item["newfile"], "old-2.jar")


if __name__ == "__main__":
    unittest.main()
