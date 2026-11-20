import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cds  # noqa: E402


class BaseCdsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runtime = self.tmp / "runtime"
        self.bin = self.runtime / "java-runtime-delta" / "windows-x64" / "bin"
        (self.bin / "server").mkdir(parents=True)
        (self.bin / "server" / "jvm.dll").write_bytes(b"")
        self.javaw = self.bin / "javaw.exe"
        self.javaw.write_bytes(b"")
        (self.bin / "java.exe").write_bytes(b"")

    def fake_run(self, cmd, **kw):
        name = "classes_nocoops.jsa" if "-XX:-UseCompressedOops" in cmd else "classes.jsa"
        (self.bin / "server" / name).write_bytes(b"jsa")
        self.cmds.append(cmd)
        return mock.Mock(returncode=0)

    def test_creates_both_archives_once_with_console_java(self):
        self.cmds = []
        with mock.patch.object(cds.subprocess, "run", side_effect=self.fake_run):
            self.assertEqual(cds.ensure_base_cds(self.javaw, self.runtime), ["classes.jsa", "classes_nocoops.jsa"])
            self.assertEqual(cds.ensure_base_cds(self.javaw, self.runtime), [], "второй раз ничего не делает")
        self.assertEqual(len(self.cmds), 2)
        self.assertTrue(all(c[0].endswith("java.exe") and c[-1] == "-Xshare:dump" for c in self.cmds))

    def test_foreign_java_untouched(self):
        other = self.tmp / "Program Files" / "Java" / "bin"
        (other / "server").mkdir(parents=True)
        (other / "server" / "jvm.dll").write_bytes(b"")
        with mock.patch.object(cds.subprocess, "run") as run:
            self.assertEqual(cds.ensure_base_cds(other / "javaw.exe", self.runtime), [])
        run.assert_not_called()

    def test_failure_does_not_raise(self):
        with mock.patch.object(cds.subprocess, "run", side_effect=OSError("нет доступа")):
            logs = []
            self.assertEqual(cds.ensure_base_cds(self.javaw, self.runtime, logs.append), [])
        self.assertTrue(logs)


if __name__ == "__main__":
    unittest.main()
