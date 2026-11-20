"""«Картинка не так?» (core/visualfix.py, core/emfmodels.py): слои пака, отключение моделей, откат."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core import emfmodels, visualfix


def make_pack(folder: Path, name: str, files: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as z:
        z.writestr("pack.mcmeta", "{}")
        for f in files:
            z.writestr(f, b"x")


class VisualFixTests(unittest.TestCase):
    def setUp(self):
        self.g = Path(tempfile.mkdtemp())
        (self.g / "mods").mkdir()
        (self.g / "config").mkdir()
        for jar in ("entity_model_features-3.3.12-1.21-fabric.jar", "entity_texture_features-7.2.7-1.21-fabric.jar",
                    "continuity-3.0.0+1.21.1.jar"):
            (self.g / "mods" / jar).write_bytes(b"x")
        make_pack(self.g / "resourcepacks", "Pack.zip", [
            "assets/minecraft/optifine/cem/bell.jem", "assets/minecraft/optifine/cem/chest.jem",
            "assets/minecraft/optifine/ctm/glass/glass.properties", "assets/minecraft/textures/block/stone_n.png"])
        make_pack(self.g / "resourcepacks", "Off.zip", ["assets/minecraft/optifine/cem/creeper.jem"])
        (self.g / "options.txt").write_text('resourcePacks:["vanilla","file/Pack.zip"]\n', encoding="utf-8")
        (self.g / "config" / "entity_model_features.json").write_text(
            json.dumps({"allowedCEM": "ALL", "modelsNamesDisabled": []}), encoding="utf-8")
        (self.g / "config" / "entity_texture_features.json").write_text(
            json.dumps({"enableCustomTextures": True, "enableEmissiveTextures": True}), encoding="utf-8")
        (self.g / "config" / "continuity.json").write_text(json.dumps({"connected_textures": True}), encoding="utf-8")

    def cfg(self, name):
        return json.loads((self.g / "config" / name).read_text(encoding="utf-8"))

    def test_scan_reads_only_enabled_packs_and_features(self):
        d = visualfix.scan(self.g)
        self.assertEqual([p["name"] for p in d["packs"]], ["Pack.zip"])
        feats = {f["id"] for f in d["packs"][0]["features"]}
        self.assertTrue({"cem", "ctm", "pbr"} <= feats)
        self.assertEqual({m["name"] for m in d["models"]}, {"bell", "chest"})     # creeper — из выключенного пака
        self.assertTrue(d["emf"])
        ids = {x["id"] for x in d["layers"]}
        self.assertIn("etf.textures", ids)
        self.assertIn("continuity.ctm", ids)
        self.assertNotIn("ebe.chests", ids)                                       # мода EBE нет

    def test_layer_off_on_and_reset(self):
        self.assertTrue(visualfix.set_layer(self.g, "etf.textures", False)["ok"])
        self.assertFalse(self.cfg("entity_texture_features.json")["enableCustomTextures"])
        self.assertTrue(self.cfg("entity_texture_features.json")["enableEmissiveTextures"])   # чужие настройки целы
        self.assertTrue(visualfix.scan(self.g)["changed"])
        res = visualfix.reset_all(self.g)
        self.assertTrue(res["ok"])
        self.assertTrue(self.cfg("entity_texture_features.json")["enableCustomTextures"])
        self.assertFalse(visualfix.scan(self.g)["changed"])
        self.assertFalse((self.g / visualfix.STATE).exists())

    def test_layer_turned_back_on_forgets_change(self):
        visualfix.set_layer(self.g, "continuity.ctm", False)
        visualfix.set_layer(self.g, "continuity.ctm", True)
        self.assertFalse(visualfix.scan(self.g)["changed"])

    def test_models_disable_enable_and_backup(self):
        visualfix.set_models(self.g, ["bell"], [])
        self.assertEqual(self.cfg("entity_model_features.json")["modelsNamesDisabled"], ["bell"])
        self.assertTrue((self.g / "config" / emfmodels.BACKUP).exists())
        self.assertEqual(self.cfg("entity_model_features.json")["allowedCEM"], "ALL")
        d = visualfix.scan(self.g)
        self.assertEqual([m["name"] for m in d["models"] if m["disabled"]], ["bell"])
        visualfix.reset_all(self.g)
        self.assertEqual(self.cfg("entity_model_features.json")["modelsNamesDisabled"], [])

    def test_reset_keeps_models_disabled_by_hand(self):
        cfg = self.g / "config" / "entity_model_features.json"
        cfg.write_text(json.dumps({"modelsNamesDisabled": ["bell"]}), encoding="utf-8")   # отключено не отсюда
        visualfix.set_models(self.g, ["chest"], [])
        visualfix.reset_all(self.g)
        self.assertEqual(self.cfg("entity_model_features.json")["modelsNamesDisabled"], ["bell"])

    def test_missing_config_gives_error_not_crash(self):
        (self.g / "config" / "continuity.json").unlink()
        self.assertIn("error", visualfix.set_layer(self.g, "continuity.ctm", False))
        self.assertIn("error", visualfix.set_layer(self.g, "nope", False))

    def test_properties_layer(self):
        (self.g / "mods" / "enhancedblockentities-0.9+1.20.jar").write_bytes(b"x")
        (self.g / "config" / "enhanced_bes.properties").write_text("#c\nrender_enhanced_chests=true\nbed_ao=false\n", encoding="utf-8")
        visualfix.set_layer(self.g, "ebe.chests", False)
        text = (self.g / "config" / "enhanced_bes.properties").read_text(encoding="utf-8")
        self.assertIn("render_enhanced_chests=false", text)
        self.assertIn("bed_ao=false", text)
        visualfix.reset_all(self.g)
        self.assertIn("render_enhanced_chests=true", (self.g / "config" / "enhanced_bes.properties").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
