"""Проверки перед запуском и флаги JVM: свободная память, 32-битная Java, короткие заголовки объектов."""
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.launch import G1_FLAGS, GameRunner, compact_headers_flags, pick_gc_flags  # noqa: E402
from core.utils import java_is_32bit, low_memory_warning  # noqa: E402


def fake_exe(path: Path, machine: int) -> None:
    pe = 0x80
    data = bytearray(0x200)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = struct.pack("<I", pe)
    data[pe:pe + 4] = b"PE\0\0"
    data[pe + 4:pe + 6] = struct.pack("<H", machine)
    path.write_bytes(bytes(data))


class LowMemoryTests(unittest.TestCase):
    def test_enough_memory_no_warning(self):
        self.assertEqual(low_memory_warning(4, 8000), "")

    def test_threshold_is_ninety_percent(self):
        self.assertEqual(low_memory_warning(4, 3700), "")            # 4096 * 0.9 = 3686
        self.assertIn("4 ГБ", low_memory_warning(4, 3600))

    def test_unknown_memory_no_warning(self):
        self.assertEqual(low_memory_warning(4, None), "")
        self.assertEqual(low_memory_warning(4, 0), "")


class Java32Tests(unittest.TestCase):
    def test_pe_header(self):
        with tempfile.TemporaryDirectory() as d:
            x86, x64 = Path(d) / "a.exe", Path(d) / "b.exe"
            fake_exe(x86, 0x014C)
            fake_exe(x64, 0x8664)
            self.assertTrue(java_is_32bit(str(x86)))
            self.assertFalse(java_is_32bit(str(x64)))

    def test_release_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "bin").mkdir()
            (root / "release").write_text('OS_ARCH="x86"\n', encoding="utf-8")
            self.assertTrue(java_is_32bit(str(root / "bin" / "java")))
            (root / "release").write_text('OS_ARCH="x86_64"\n', encoding="utf-8")
            self.assertFalse(java_is_32bit(str(root / "bin" / "java")))

    def test_missing_file(self):
        self.assertFalse(java_is_32bit("C:/нет/такого/java.exe"))


class FlagsTests(unittest.TestCase):
    def run_gc(self, java_major, ram_gb=4, cores=4):
        import os
        real = os.cpu_count
        os.cpu_count = lambda: cores
        try:
            logs = []
            runner = types.SimpleNamespace(_java_major=java_major, _log=logs.append,
                                           config=types.SimpleNamespace(optimize_jvm=True, extra_jvm_args=""))
            plan = types.SimpleNamespace(java_major=java_major, command=["java", *G1_FLAGS, "net.minecraft.Main"])
            target = types.SimpleNamespace(compat=False, extra_jvm="", ram_gb=ram_gb)
            GameRunner._apply_gc(runner, plan, target)
            return plan.command
        finally:
            os.cpu_count = real

    def test_g1_has_mojang_flags(self):
        self.assertIn("-XX:+UseStringDeduplication", G1_FLAGS)
        self.assertIn("-XX:+AlwaysPreTouch", G1_FLAGS)

    def test_compact_headers_only_java24_plus(self):
        self.assertEqual(compact_headers_flags(21), [])
        self.assertEqual(compact_headers_flags(24), ["-XX:+UnlockExperimentalVMOptions", "-XX:+UseCompactObjectHeaders"])
        self.assertEqual(compact_headers_flags(25), ["-XX:+UseCompactObjectHeaders"])

    def test_java21_no_compact_headers(self):
        self.assertNotIn("-XX:+UseCompactObjectHeaders", self.run_gc(21))

    def test_java25_g1_gets_compact_headers_after_g1_flags(self):
        cmd = self.run_gc(25)
        self.assertIn("-XX:+UseCompactObjectHeaders", cmd)
        self.assertEqual(cmd[-1], "net.minecraft.Main")
        self.assertEqual(cmd.count("-XX:+UnlockExperimentalVMOptions"), 1)

    def test_java25_zgc(self):
        cmd = self.run_gc(25, ram_gb=8, cores=8)
        self.assertIn("-XX:+UseZGC", cmd)
        self.assertIn("-XX:+UseCompactObjectHeaders", cmd)
        self.assertNotIn("-XX:+UseG1GC", cmd)

    def test_java8_untouched(self):
        cmd = self.run_gc(8)
        self.assertEqual(cmd, ["java", *G1_FLAGS, "net.minecraft.Main"])

    def test_zgc_list_has_dedup(self):
        self.assertIn("-XX:+UseStringDeduplication", pick_gc_flags(21, 8, 8))


if __name__ == "__main__":
    unittest.main()
