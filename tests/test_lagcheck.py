"""«Почему тормозит»: разбор latest.log и журнала сборщика мусора."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import lagcheck  # noqa: E402


def g1(ms, kind="Young (Normal) (G1 Evacuation Pause)", up="10.5"):
    return f"[2026-11-17T13:44:21.482+0300][{up}s] GC(1) Pause {kind} 120M->30M(512M) {ms}ms"


class LagCheckTests(unittest.TestCase):
    def test_cant_keep_up_found_with_real_log_line(self):
        log = "\n".join(["[21:36:13] [Server thread/WARN]: Can't keep up! Is the server overloaded? Running 2061ms or 41 ticks behind",
                         "[21:36:46] [Server thread/WARN]: Can't keep up! Is the server overloaded? Running 2371ms or 47 ticks behind"])
        r = lagcheck.analyze(log, ram_gb=6)
        self.assertEqual(r["behind"], 2)
        self.assertEqual(r["verdict"], "warn")
        self.assertIn("2,4", r["findings"][0]["text"].replace(".", ","))

    def test_long_gc_pauses_suggest_more_memory(self):
        gc = "\n".join(g1(12.3) for _ in range(5)) + "\n" + g1(812.4, "Full (G1 Compaction Pause)") * 1 + "\n" + g1(640.0)
        r = lagcheck.analyze("", gc, ram_gb=4)
        f = r["findings"][0]
        self.assertEqual((f["fix"]["action"], f["fix"]["arg"]), ("ram", 6))
        self.assertEqual(r["verdict"], "warn" if f["level"] == "warn" else "bad")
        self.assertGreaterEqual(r["gc"]["over500"], 2)

    def test_healthy_gc_gives_no_complaints(self):
        gc = "\n".join(g1(4.2) for _ in range(50))
        r = lagcheck.analyze("", gc, ram_gb=6)
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["verdict"], "ok")

    def test_zgc_pause_lines_parsed(self):
        gc = "\n".join(f"[2026-11-17T13:44:21.482+0300][{i}.0s] GC({i}) Pause Mark Start 0.0{i}ms" for i in range(1, 6))
        st = lagcheck.gc_stats(gc)
        self.assertEqual(st["count"], 5)
        self.assertLess(st["max_ms"], 1)

    def test_too_much_memory_noted_only_when_pauses_tiny(self):
        gc = "\n".join(g1(3.0) for _ in range(30))
        r = lagcheck.analyze("", gc, ram_gb=12, mods_count=10)
        self.assertEqual(r["findings"][0]["level"], "info")

    def test_error_spam_from_one_mod(self):
        log = "\n".join("[10:00:00] [SomeMod/ERROR]: Failed to tick entity" for _ in range(300))
        r = lagcheck.analyze(log)
        self.assertIn("SomeMod", r["findings"][0]["title"])

    def test_empty_logs(self):
        r = lagcheck.analyze("", "")
        self.assertEqual((r["verdict"], r["has_game_log"], r["has_gc_log"]), ("ok", False, False))

    def test_read_game_log_limits_size(self):
        with tempfile.TemporaryDirectory() as d:
            logs = Path(d) / "logs"
            logs.mkdir()
            (logs / "latest.log").write_text("x\n" * (lagcheck.MAX_READ // 2 + 1000), encoding="utf-8")
            self.assertLessEqual(len(lagcheck.read_game_log(Path(d))), lagcheck.MAX_READ)

    def test_real_logs_on_this_pc_do_not_crash(self):
        base = Path.home() / ".vaultmc" / "instances"
        dirs = list(base.glob("*/minecraft")) if base.is_dir() else []
        if not dirs:
            self.skipTest("нет настоящих журналов на этом ПК")
        for d in dirs:
            r = lagcheck.analyze(lagcheck.read_game_log(d), ram_gb=6, java_major=21)
            self.assertIn(r["verdict"], ("ok", "warn", "bad"))


if __name__ == "__main__":
    unittest.main()
