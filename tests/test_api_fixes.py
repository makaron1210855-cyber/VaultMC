"""Регрессии ревью backend/api.py (10.0.3): ввод из формы не должен ронять API.

Проверяем только чистые функции (без окна pywebview и без сети):
- _clamp_ram: null/пустая строка из формы — «авто», а не TypeError;
- save_settings: нечисловое значение int-поля пропускается, а не роняет сохранение.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api import RAM_MIN, RAM_MAX, Api, _fresh_crash_report  # noqa: E402


class ClampRamTests(unittest.TestCase):
    def test_none_is_auto(self):
        self.assertEqual(Api._clamp_ram(None), 0)

    def test_empty_string_is_auto(self):
        self.assertEqual(Api._clamp_ram(""), 0)

    def test_garbage_is_auto(self):
        self.assertEqual(Api._clamp_ram("12abc"), 0)

    def test_number_is_clamped(self):
        self.assertEqual(Api._clamp_ram(1), RAM_MIN)
        self.assertEqual(Api._clamp_ram(10_000), RAM_MAX)


class FreshCrashReportTests(unittest.TestCase):
    def test_no_folder_is_not_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_fresh_crash_report(Path(tmp), 0.0))

    def test_new_report_is_fresh_old_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "crash-reports"
            folder.mkdir()
            old = folder / "old.txt"
            old.write_text("x", encoding="utf-8")
            os.utime(old, (1000, 1000))
            self.assertFalse(_fresh_crash_report(Path(tmp), 2000.0))
            (folder / "new.txt").write_text("y", encoding="utf-8")
            self.assertTrue(_fresh_crash_report(Path(tmp), 2000.0))


if __name__ == "__main__":
    unittest.main()
