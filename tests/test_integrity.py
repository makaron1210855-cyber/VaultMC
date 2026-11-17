"""Проверка файлов лаунчера (core/integrity.py) и скрипт отката (installer/rollback.cmd)."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import integrity  # noqa: E402


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = Path(self._tmp.name) / "app"
        (self.app / "core" / "__pycache__").mkdir(parents=True)
        (self.app / "main.py").write_text("print(1)", encoding="utf-8")
        (self.app / "core" / "utils.py").write_text("X = 1", encoding="utf-8")
        (self.app / "core" / "__pycache__" / "utils.cpython-312.pyc").write_bytes(b"pyc")
        self.count = integrity.build(self.app, "9.9.9")

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_install_has_no_problems(self):
        self.assertEqual(self.count, 2, "кэш Python и сам files.json не считаются")
        self.assertEqual(integrity.check(self.app), [])

    def test_changed_and_missing_files_found(self):
        (self.app / "core" / "utils.py").write_text("X = 2", encoding="utf-8")
        (self.app / "main.py").unlink()
        bad = integrity.check(self.app)
        self.assertEqual(sorted(bad), ["core/utils.py — изменён", "main.py — нет файла"])

    def test_new_pycache_is_ignored(self):
        (self.app / "core" / "__pycache__" / "new.pyc").write_bytes(b"x")
        self.assertEqual(integrity.check(self.app), [])

    def test_no_manifest_means_no_check(self):
        (self.app / integrity.MANIFEST).unlink()
        self.assertEqual(integrity.check(self.app), [])


@unittest.skipUnless(sys.platform == "win32", "откат — только Windows")
class RollbackScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.inst = Path(self._tmp.name) / "VaultMC"
        for name, ver in (("app", "NEW"), ("app.prev", "OLD")):
            (self.inst / name).mkdir(parents=True)
            (self.inst / name / "main.py").write_text(ver, encoding="utf-8")
        for name, ver in (("python", "NEWPY"), ("python.prev", "OLDPY")):
            (self.inst / name).mkdir()
            (self.inst / name / "VaultMC.exe").write_text(ver, encoding="utf-8")
        script = self.inst / "rollback.cmd"
        # без запуска лаунчера в конце: в тесте заменяем строку «start ...» на пустую команду
        text = (ROOT / "installer" / "rollback.cmd").read_text(encoding="ascii")
        script.write_text(text.replace('start "" "%~dp0python\\VaultMC.exe" "%~dp0app\\main.py"', "rem start skipped in test")
                          .replace("timeout /t 2 /nobreak >nul", "rem").replace("taskkill /IM VaultMC.exe /F >nul 2>&1", "rem"),
                          encoding="ascii")
        self.script = script

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self):
        return subprocess.run(["cmd", "/c", str(self.script)], cwd=str(self.inst), capture_output=True, text=True, timeout=60)

    def test_swaps_folders(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.inst / "app" / "main.py").read_text(), "OLD")
        self.assertEqual((self.inst / "python" / "VaultMC.exe").read_text(), "OLDPY")
        self.assertFalse((self.inst / "app.bad").exists())
        self.assertFalse((self.inst / "app.prev").exists())

    def test_nothing_to_roll_back(self):
        shutil.rmtree(self.inst / "app.prev")
        script = self.script.read_text(encoding="ascii").replace("timeout /t 6 >nul", "rem")
        self.script.write_text(script, encoding="ascii")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual((self.inst / "app" / "main.py").read_text(), "NEW", "текущая версия не тронута")


if __name__ == "__main__":
    unittest.main()
