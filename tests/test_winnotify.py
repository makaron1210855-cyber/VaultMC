"""Уведомления Windows: скрипт статичен, текст идёт через окружение и экранируется (проверка настоящим PowerShell)."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import winnotify  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "нужен Windows и PowerShell")
class ToastXmlTests(unittest.TestCase):
    def texts(self, title, text, launch="vaultmc://friends"):
        root = ET.fromstring(winnotify.preview_xml(title, text, launch))
        return [t.text for t in root.iter("text")], root.attrib["launch"]

    def test_plain_text_roundtrip(self):
        texts, launch = self.texts("Steve хочет добавить вас в друзья", "Зайдите в лаунчер")
        self.assertEqual(texts, ["Steve хочет добавить вас в друзья", "Зайдите в лаунчер"])
        self.assertEqual(launch, "vaultmc://friends")

    def test_hostile_nick_stays_text(self):
        evil = "<script>'\"& $(calc.exe) `whoami` ; Remove-Item *"
        texts, _ = self.texts(evil, evil)
        self.assertEqual(texts, [evil, evil], "всё пришло как обычный текст, ничего не выполнилось и xml не сломан")

    def test_hostile_launch_attribute(self):
        _, launch = self.texts("a", "b", "x' onclick='evil")
        self.assertEqual(launch, "x' onclick='evil", "кавычка в атрибуте экранирована, а не открывает новый атрибут")

    def test_script_has_no_user_data(self):
        self.assertNotIn("{title}", winnotify.SCRIPT)
        for var in ("VMC_TITLE", "VMC_TEXT", "VMC_LAUNCH", "VMC_APPID"):
            self.assertIn(f"$env:{var}", winnotify.SCRIPT)

    def test_long_text_trimmed(self):
        texts, _ = self.texts("x" * 500, "y" * 500)
        self.assertEqual((len(texts[0]), len(texts[1])), (120, 240))


class FlashTests(unittest.TestCase):
    def test_no_window_means_false(self):
        self.assertFalse(winnotify.flash_window("Окно, которого нет 12345"))


if __name__ == "__main__":
    unittest.main()
