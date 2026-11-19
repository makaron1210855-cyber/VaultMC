"""«Починка» и разбор вылета не должны показывать то, что уже починено или не было вылетом."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import repair  # noqa: E402
from core.crash import analyze, root_cause  # noqa: E402

OOM_LOG = "[Render thread/ERROR]: java.lang.OutOfMemoryError: Java heap space\n"


class RepairLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.game = Path(self._tmp.name)
        (self.game / "logs").mkdir()
        (self.game / "mods").mkdir()
        self.log = self.game / "logs" / "latest.log"

    def write_log(self, text):
        """Журнал свежее папки модов — как после настоящего запуска (отметки времени на диске грубые, поэтому задаём сами)."""
        self.log.write_text(text, encoding="utf-8")
        later = time.time() + 5
        os.utime(self.log, (later, later))

    def tearDown(self):
        self._tmp.cleanup()

    def titles(self):
        return [i["title"] for i in repair._log_issues(self.game, self.game / "mods", 4, set())]

    def test_crash_log_is_reported(self):
        self.write_log(OOM_LOG)
        self.assertIn("Игре не хватило памяти", self.titles())

    def test_fix_hides_old_log_findings(self):
        self.write_log(OOM_LOG)
        past = time.time() - 60
        os.utime(self.log, (past, past))
        repair.mark_fixed(self.game)
        self.assertEqual(self.titles(), [])

    def test_log_after_fix_is_reported_again(self):
        repair.mark_fixed(self.game)
        time.sleep(0.05)
        self.write_log(OOM_LOG)
        future = time.time() + 5
        os.utime(self.log, (future, future))
        self.assertIn("Игре не хватило памяти", self.titles())

    def test_clean_exit_is_not_a_crash(self):
        self.write_log("[Render thread/WARN]: caught java.lang.OutOfMemoryError once\n"
                            "[Render thread/INFO]: Stopping!\n")
        self.assertEqual(self.titles(), [])

    def test_stopping_after_crash_is_still_a_crash(self):
        self.write_log(OOM_LOG + "#@!@# Game crashed! Crash report saved to: x\n[Render thread/INFO]: Stopping!\n")
        self.assertIn("Игре не хватило памяти", self.titles())

    def test_report_older_than_last_launch_is_ignored(self):
        reports = self.game / "crash-reports"
        reports.mkdir()
        rep = reports / "crash-1.txt"
        rep.write_text("Description: Ticking entity\n\njava.lang.OutOfMemoryError: x\n")
        old = time.time() - 7200
        os.utime(rep, (old, old))
        self.write_log("[Render thread/INFO]: Stopping!\n")   # создан позже отчёта — запуск удался
        self.assertEqual(repair._newest_report(self.game), "")


class RootCauseTests(unittest.TestCase):
    def test_report_description_wins(self):
        rep = "Description: Initializing game\n\njava.lang.IllegalStateException: Failed to bind\n\tat x\n"
        self.assertIn("Initializing game", root_cause("", rep))

    def test_last_caused_by_not_first_exception(self):
        log = ("java.io.IOException: harmless warning at startup\n" + "line\n" * 5 +
               "java.lang.RuntimeException: wrapper\nCaused by: java.lang.NoSuchMethodError: a.b()\n"
               "Caused by: java.lang.ClassNotFoundException: deep.Root\n")
        self.assertIn("ClassNotFoundException", root_cause(log))

    def test_unknown_uses_root_cause(self):
        log = "java.io.IOException: harmless\n" + "x\n" * 5 + "Caused by: java.lang.ArithmeticException: real\n"
        res = analyze(log)
        self.assertIn("ArithmeticException", res[0]["text"])
        self.assertNotIn("harmless", res[0]["text"])


if __name__ == "__main__":
    unittest.main()
