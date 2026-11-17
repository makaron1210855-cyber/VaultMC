"""Разбор вылетов: новые правила (этап 6). Примеры — настоящие сообщения Java, Minecraft, Fabric и Forge."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.crash import analyze  # noqa: E402


def keys(findings):
    return [f["title"] for f in findings]


class CrashRuleTests(unittest.TestCase):
    def one(self, text, **kw):
        res = analyze(text, **kw)
        self.assertEqual(len(res), 1, keys(res))
        return res[0]

    def test_unknown_jvm_flag(self):
        f = self.one("Unrecognized VM option 'UseShenandoahGC'\nError: Could not create the Java Virtual Machine.\n"
                     "Error: A fatal exception has occurred. Program will exit.")
        self.assertEqual(f["fix"]["action"], "clear_jvm")
        self.assertIn("UseShenandoahGC", f["text"])

    def test_heap_reserve_is_not_jvm_flags(self):
        f = self.one("Error occurred during initialization of VM\nCould not reserve enough space for 8388608KB object heap\n"
                     "Error: Could not create the Java Virtual Machine.")
        self.assertEqual(f["fix"]["action"], "ram_down")

    def test_disk_full_english_and_russian(self):
        self.assertIn("диске", self.one("java.io.IOException: No space left on device")["title"])
        self.assertIn("диске", self.one("java.io.IOException: На диске недостаточно места")["title"])

    def test_native_thread_suggests_less_memory_not_more(self):
        f = self.one("java.lang.OutOfMemoryError: unable to create native thread: possibly out of memory or process/resource limits", ram_gb=8)
        self.assertEqual(f["fix"]["action"], "ram_down")
        self.assertEqual(f["fix"]["arg"], 6)

    def test_metaspace(self):
        f = self.one("java.lang.OutOfMemoryError: Metaspace")
        self.assertIn("Metaspace", f["text"])

    def test_heap_oom_still_suggests_more(self):
        f = self.one("java.lang.OutOfMemoryError: Java heap space", ram_gb=4)
        self.assertEqual((f["fix"]["action"], f["fix"]["arg"]), ("ram", 6))

    def test_lwjgl_natives(self):
        self.assertIn("библиотеки", self.one("java.lang.UnsatisfiedLinkError: Failed to locate library: lwjgl.dll")["title"])

    def test_corrupt_world(self):
        f = self.one("java.util.zip.ZipException: Not in GZIP format\n\tat net.minecraft.nbt.NbtIo.readCompressed\n"
                     "Failed to read saves/New World/level.dat")
        self.assertEqual(f["fix"]["action"], "open_worlds")
        self.assertEqual(self.one("Exception reading C:\\saves\\W\\region\\r.0.0.mca")["fix"]["action"], "open_worlds")

    def test_gzip_error_without_world_is_not_world(self):
        res = analyze("java.util.zip.ZipException: Not in GZIP format while reading http response")
        self.assertNotIn("Повреждён мир", keys(res))

    def test_fabric_loader_too_old(self):
        f = self.one("Mod 'Sodium' (sodium) 0.5.8 requires version >=0.15.10 of 'Fabric Loader' (fabricloader), "
                     "but only the wrong version is present: 0.14.21!")
        self.assertIn("Fabric Loader", f["title"])
        self.assertIn("0.14.21", f["text"])

    def test_fabric_api_too_old_offers_search(self):
        f = self.one("Mod 'Iris' (iris) 1.7.0 requires version >=0.100.0 of 'Fabric API' (fabric-api), "
                     "but only the wrong version is present: 0.90.0+1.20.1!")
        self.assertEqual((f["fix"]["action"], f["fix"]["arg"]), ("find_mod", "fabric-api"))

    def test_mod_needs_other_java(self):
        f = self.one("Mod 'Create' (create) 0.5 requires version >=21 of 'Java' (java), but only the wrong version is present: 17!")
        self.assertEqual(f["fix"]["action"], "java_auto")

    def test_forge_too_old(self):
        f = self.one("Mod ID: 'forge', Requested by: 'create', Expected range: '[47.2.0,)', Actual version: '47.1.0'")
        self.assertIn("create", f["title"])

    def test_forge_mod_instance_disables_mod(self):
        with tempfile.TemporaryDirectory() as d:
            mods = Path(d)
            (mods / "jei-1.20.1-forge-15.0.jar").write_bytes(b"x")
            f = self.one("net.minecraftforge.fml.ModLoadingException: Failed to create mod instance. ModID: jei, class mezz.jei.Foo",
                         mods_dir=mods)
        self.assertEqual(f["fix"]["action"], "disable_mod")
        self.assertEqual(f["fix"]["arg"], "jei-1.20.1-forge-15.0.jar")

    def test_corrupt_jar_names_file_in_mods(self):
        with tempfile.TemporaryDirectory() as d:
            mods = Path(d)
            (mods / "broken-mod-1.0.jar").write_bytes(b"x")
            f = self.one("Failed to read mod JAR: mods/broken-mod-1.0.jar\njava.util.zip.ZipException: zip END header not found", mods_dir=mods)
        self.assertEqual(f["fix"]["arg"], "broken-mod-1.0.jar")

    def test_port_in_use(self):
        self.assertIn("Порт", self.one("java.net.BindException: Address already in use: bind")["title"])

    def test_windows_exit_codes_signed_and_unsigned(self):
        for code in (-1073741819, 3221225477, 0xC0000005):
            f = self.one("[Render thread/INFO]: Stopping!", exit_code=code)
            self.assertIn("0xC0000005", f["title"])
            self.assertEqual(f["fix"]["action"], "compat_on")

    def test_exit_code_not_added_when_driver_already_blamed(self):
        res = analyze("# C  [ig9icd64.dll+0x1234]", exit_code=-1073741819)
        self.assertEqual(keys(res), ["Проблема с драйвером видеокарты"])

    def test_normal_exit_codes_say_nothing_special(self):
        self.assertEqual(keys(analyze("Stopping!", exit_code=1)), ["Причину не удалось определить"])

    def test_real_crash_report_still_works(self):
        sample = Path.home() / ".vaultmc" / "instances"
        reports = list(sample.glob("*/minecraft/crash-reports/crash-*.txt")) if sample.is_dir() else []
        if not reports:
            self.skipTest("нет настоящих отчётов на этом ПК")
        res = analyze("", reports[0].read_text(encoding="utf-8", errors="replace"))
        self.assertTrue(res and all("title" in f and "text" in f for f in res))


if __name__ == "__main__":
    unittest.main()
