"""«Лишние моды» (core/optaudit.py) и отмена переноса модов (core/modcopy.undo): только то, что доказуемо лишнее."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core import modcopy, optaudit


def jar(folder: Path, name: str, mod_id: str, version: str = "1.0.0", depends: dict | None = None,
        nested: list | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    meta = {"schemaVersion": 1, "id": mod_id, "version": version, "name": mod_id.title(),
            "depends": {"minecraft": "1.21.1", **(depends or {})}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))
        for n in nested or []:                                    # вшитая библиотека (jar-in-jar)
            z.writestr(f"META-INF/jars/{n}.jar", b"x")
    return path


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def scan(self):
        return optaudit.scan(self.mods, "fabric", "1.21.1")

    def kinds(self):
        return {f["file"]: f["kind"] for f in self.scan()}

    def test_clean_pack_has_nothing(self):
        jar(self.mods, "sodium.jar", "sodium", "0.6.0")
        jar(self.mods, "lithium.jar", "lithium")
        jar(self.mods, "ferritecore.jar", "ferritecore")
        self.assertEqual(self.scan(), [])

    def test_indium_useless_with_new_sodium_but_needed_with_old(self):
        jar(self.mods, "sodium.jar", "sodium", "0.6.5")
        jar(self.mods, "indium.jar", "indium")
        self.assertEqual(self.kinds(), {"indium.jar": "obsolete"})
        (self.mods / "sodium.jar").unlink()
        jar(self.mods, "sodium-old.jar", "sodium", "0.5.8")           # другое имя: список модов кэшируется по имени и размеру
        self.assertEqual(self.scan(), [])

    def test_two_renderers_and_lighting_engines(self):
        jar(self.mods, "sodium.jar", "sodium", "0.6.0")
        jar(self.mods, "rubidium.jar", "rubidium")
        jar(self.mods, "scalablelux.jar", "scalablelux")
        jar(self.mods, "phosphor.jar", "phosphor")
        self.assertEqual(self.kinds(), {"rubidium.jar": "twin", "phosphor.jar": "twin"})

    def test_optifabric_without_optifine(self):
        jar(self.mods, "optifabric.jar", "optifabric")
        self.assertEqual(self.kinds(), {"optifabric.jar": "orphan"})
        (self.mods / "OptiFine_1.21.1_HD_U_J1.jar").write_bytes(b"x")
        self.assertNotIn("optifabric.jar", self.kinds())

    def test_unused_library_flagged_used_not(self):
        jar(self.mods, "cloth.jar", "cloth-config")
        jar(self.mods, "arch.jar", "architectury")
        jar(self.mods, "needs.jar", "needsarch", depends={"architectury": "*"})
        self.assertEqual(self.kinds(), {"cloth.jar": "library"})

    def test_library_bundled_inside_other_mod_does_not_make_it_a_library(self):
        # у мода «blur» внутри вшит MidnightLib: он числится в ids, но сам не библиотека
        jar(self.mods, "blur.jar", "blur", nested=["midnightlib"])
        self.assertEqual(self.scan(), [])

    def test_known_conflict_and_duplicate(self):
        jar(self.mods, "sodium.jar", "sodium", "0.6.0")
        (self.mods / "OptiFine_1.21.1.jar").write_bytes(b"x")
        self.assertEqual(self.kinds().get("OptiFine_1.21.1.jar"), "conflict")

    def test_apply_disables_and_is_reversible(self):
        jar(self.mods, "cloth.jar", "cloth-config")
        res = optaudit.apply(self.mods, "fabric", "1.21.1", ["cloth.jar"])
        self.assertEqual(res["disabled"], ["cloth.jar"])
        self.assertTrue((self.mods / "cloth.jar.disabled").exists())
        self.assertFalse((self.mods / "cloth.jar").exists())

    def test_apply_refuses_mod_that_others_need(self):
        jar(self.mods, "arch.jar", "architectury")
        jar(self.mods, "needs.jar", "needsarch", depends={"architectury": "*"})
        res = optaudit.apply(self.mods, "fabric", "1.21.1", ["arch.jar"])
        self.assertEqual(res["disabled"], [])
        self.assertEqual(res["blocked"][0]["file"], "arch.jar")
        self.assertTrue((self.mods / "arch.jar").exists())


class UndoTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.src, self.dst = root / "src", root / "dst"
        self.mods = self.dst / "mods"
        jar(self.mods, "keep.jar", "keep")
        jar(self.src / "mods", "new1.jar", "new1")
        jar(self.src / "mods", "lib.jar", "somelib")
        jar(self.src / "mods", "new2.jar", "new2", depends={"somelib": "*"})

    def run_install(self, files):
        items = modcopy.plan(self.src, "fabric", "1.21.1", self.dst, "fabric", "1.21.1", log=lambda m: None)["items"]
        return modcopy.install(self.src, self.dst, "fabric", "1.21.1", items, files, log=lambda m: None, source_name="Источник")

    def test_history_and_undo_removes_only_that_batch(self):
        r = self.run_install(["new2.jar"])
        self.assertEqual(len(r["installed"]), 2)                          # new2 и его библиотека somelib
        h = modcopy.history(self.dst)
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["source"], "Источник")
        res = modcopy.undo(self.dst, "fabric", "1.21.1", h[0]["id"], log=lambda m: None)
        self.assertEqual(sorted(res["removed"]), sorted(r["installed"]))
        self.assertEqual(sorted(f.name for f in self.mods.iterdir() if f.suffix == ".jar"), ["keep.jar"])
        self.assertEqual(modcopy.history(self.dst), [])

    def test_undo_keeps_mod_that_another_mod_started_to_need(self):
        self.run_install(["lib.jar"])
        jar(self.mods, "later.jar", "later", depends={"somelib": "*"})    # потом поставили мод, которому нужна эта библиотека
        h = modcopy.history(self.dst)
        res = modcopy.undo(self.dst, "fabric", "1.21.1", h[0]["id"], log=lambda m: None)
        self.assertEqual(res["removed"], [])
        self.assertEqual(len(res["kept"]), 1)
        self.assertTrue((self.mods / "lib.jar").exists())

    def test_unknown_batch(self):
        self.assertIn("error", modcopy.undo(self.dst, "fabric", "1.21.1", "nope", log=lambda m: None))


if __name__ == "__main__":
    unittest.main()
