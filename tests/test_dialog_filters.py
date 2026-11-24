"""Подписи фильтров окна сохранения должны проходить проверку pywebview.

pywebview разбирает описание фильтра по шаблону ^([\\w ]+)\\((\\*...)\\)$ и бросает
ValueError «is not a valid file filter», если в описании есть скобки. Подписи экспорта
(«Modrinth (mrpack)» и т.п.) раньше попадали в фильтр как есть — окно не открывалось.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.packformats import EXPORT_FORMATS  # noqa: E402

# тот же шаблон, что в packaging/python/Lib/site-packages/webview/util.py (parse_file_type)
PYWEBVIEW_FILTER = re.compile(r'^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$')


def save_filter(label: str, ext: str) -> str:
    """Та же строка, что уходит в _dialog('save') в backend/api.py (export_instance)."""
    return f"{label.replace('(', '').replace(')', '')} (*{ext})"


class DialogFilterTests(unittest.TestCase):
    def test_export_labels_pass_pywebview_check(self):
        for fmt, (label, ext) in EXPORT_FORMATS.items():
            with self.subTest(fmt=fmt):
                self.assertRegex(save_filter(label, ext), PYWEBVIEW_FILTER)

    def test_raw_label_with_parentheses_is_rejected_by_pywebview(self):
        # защита от возврата старого поведения: скобки в подписи — ошибка pywebview
        self.assertNotRegex("Modrinth (mrpack) (*.mrpack)", PYWEBVIEW_FILTER)


if __name__ == "__main__":
    unittest.main()
