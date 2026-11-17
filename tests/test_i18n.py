"""Английский перевод интерфейса: ничего не осталось русским, словарь не ломается при правке текста.

Проверка настоящая: tools/i18n_scan.py достаёт из исходников все русские тексты (web/js, index.html и сообщения Python,
которые лаунчер показывает игроку) и прогоняет их через ту же функцию trText, что работает в окне лаунчера (Node).
Упала проверка — значит, добавили или изменили русский текст и не добавили/не поправили перевод в web/js/i18n.js.
Подсказка, что делать, — в сообщении теста; список можно посмотреть командой  py tools/i18n_scan.py --orphans
"""
import collections
import re
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import i18n_scan  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "нужен Node")
class TranslationTests(unittest.TestCase):
    def test_nothing_left_in_russian(self):
        left = i18n_scan.untranslated()
        shown = "\n".join(f"  {f}:{line}: {text.strip()[:100]!r}" for f, text, line, _ in left[:25])
        self.assertEqual(left, [], f"русские тексты без английского перевода ({len(left)}):\n{shown}\n"
                                   "Добавьте их в I18N_EN (web/js/i18n.js).")

    def test_no_stale_translations(self):
        stale = i18n_scan.orphans()
        shown = "\n".join(f"  {k[:100]!r}" for k in stale[:25])
        self.assertEqual(stale, [], f"в словаре есть переводы, которых нет в исходниках ({len(stale)}) — "
                                    f"русский текст правили, а перевод остался прежним:\n{shown}\n"
                                    "Исправьте ключ или удалите строку: py tools/i18n_merge.py --prune")

    def test_no_duplicate_keys(self):
        text = (ROOT / "web" / "js" / "i18n.js").read_text(encoding="utf-8")
        head = text.split("\nconst I18N = {")[0]
        keys = re.findall(r'^"((?:[^"\\]|\\.)*)":', head, re.M)
        dups = [k for k, n in collections.Counter(keys).items() if n > 1]
        self.assertEqual(dups, [], "повторяющиеся ключи словаря (последний молча перебивает прежний)")

    def test_multiline_paragraphs_translate(self):
        """Абзац, разбитый в шаблоне на строки с отступами, переводится так же, как однострочный."""
        import json
        import subprocess
        node = (
            "const vm=require('vm'),fs=require('fs');"
            "const t=vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8')+'\\n;trText',{MutationObserver:class{observe(){}disconnect(){}}});"
            "console.log(JSON.stringify(t('Без ключа всё то же самое есть в Modrinth —\\n      большинство модов лежат и там.')))"
        )
        out = subprocess.run([shutil.which("node"), "-e", node, str(ROOT / "web" / "js" / "i18n.js")],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        self.assertNotRegex(json.loads(out), "[А-Яа-яЁё]")


if __name__ == "__main__":
    unittest.main()
