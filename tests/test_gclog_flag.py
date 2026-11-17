"""Флаг журнала сборщика мусора (core/launch.py _apply_gc_log): ставится только там, где Java его понимает."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.launch import GameRunner  # noqa: E402


class GcLogFlagTests(unittest.TestCase):
    def apply(self, java_major, compat=False, command=None):
        with tempfile.TemporaryDirectory() as d:
            game_dir = Path(d)
            runner = types.SimpleNamespace(_java_major=java_major, GC_LOG=GameRunner.GC_LOG)
            plan = types.SimpleNamespace(java_major=java_major, command=command or ["java", "-Xmx4G", "net.minecraft.Main"])
            target = types.SimpleNamespace(game_dir=game_dir, compat=compat)
            GameRunner._apply_gc_log(runner, plan, target)
            return plan.command, (game_dir / "logs").is_dir()

    def test_modern_java_gets_log_right_after_java_path(self):
        cmd, logs_dir = self.apply(21)
        self.assertEqual(cmd[0], "java")
        self.assertTrue(cmd[1].startswith("-Xlog:gc:file=logs/vaultmc-gc.log:time,uptime"))
        self.assertTrue(logs_dir)
        self.assertEqual(cmd[2:], ["-Xmx4G", "net.minecraft.Main"], "остальные аргументы на месте")

    def test_java_8_unchanged_because_xlog_does_not_exist_there(self):
        cmd, _ = self.apply(8)
        self.assertFalse(any(a.startswith("-Xlog") for a in cmd))

    def test_compat_mode_unchanged(self):
        cmd, _ = self.apply(21, compat=True)
        self.assertFalse(any(a.startswith("-Xlog") for a in cmd))

    def test_user_gc_logging_not_duplicated(self):
        cmd, _ = self.apply(17, command=["java", "-Xlog:gc*:file=my.log", "-Xmx4G"])
        self.assertEqual(sum(a.startswith("-Xlog:gc") for a in cmd), 1)


if __name__ == "__main__":
    unittest.main()
