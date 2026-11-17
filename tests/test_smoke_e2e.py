"""Сквозная проверка: лаунчер поднимается в чистой «домашней» папке, настройки сохраняются, выпуск собран согласованно.

Лаунчер запускается в отдельном процессе с пустой папкой пользователя (ничего из настоящих настроек не трогаем).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from backend.events import EventBus
from backend.api import Api
api = Api(EventBus())
first = api.bootstrap()
api.save_settings({"ui_scale": 500, "theme": "neon", "crash_stats": True})
api.crash_stats_choice(True)
cfg = api.save_settings({"theme": "light", "ui_scale": 110})
print(json.dumps({"version": first["version"], "has_config": "config" in first, "theme": cfg["theme"], "scale": cfg["ui_scale"],
                  "stats": cfg["crash_stats"], "asked": cfg["crash_stats_asked"]}))
"""


class SmokeTests(unittest.TestCase):
    def test_launcher_starts_and_saves_settings_in_clean_home(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "USERPROFILE": home, "HOME": home, "APPDATA": home, "LOCALAPPDATA": home}
            r = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT)], capture_output=True, text=True, env=env,
                               encoding="utf-8", timeout=90)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(out["has_config"])
            self.assertEqual((out["theme"], out["scale"]), ("light", 110))
            self.assertTrue(out["stats"] and out["asked"])
            self.assertTrue((Path(home) / ".vaultmc" / "config.json").is_file(), "настройки записаны в чистую папку")

    def test_scale_and_theme_are_clamped(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "USERPROFILE": home, "HOME": home, "APPDATA": home, "LOCALAPPDATA": home}
            code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                    "from backend.events import EventBus; from backend.api import Api;"
                    "c = Api(EventBus()).save_settings({'ui_scale': 500, 'theme': 'neon'}); print(c['ui_scale'], c['theme'])")
            r = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, env=env, timeout=90)
            self.assertEqual(r.stdout.split(), ["125", "dark"], r.stderr[-800:])


class ReleaseConsistencyTests(unittest.TestCase):
    def test_current_version_has_changelog_and_notes(self):
        from core.changelog import CHANGELOG, notes_since
        from core.utils import APP_VERSION
        self.assertIn(APP_VERSION, CHANGELOG, "в core/changelog.py нет записи для текущей версии")
        self.assertTrue(notes_since(None, APP_VERSION).strip())

    def test_versions_page_marks_beta_and_release(self):
        import siteversions
        page = siteversions.render({"7.0": ["Первый выпуск"], "6.4.20": ["Старое"], "1.0.1": ["Совсем старое"]}, "7.0")
        self.assertIn('id="v7.0"', page)
        self.assertEqual(page.count('class="tag beta"'), 2)
        self.assertEqual(page.count('class="tag ok"'), 1)
        self.assertLess(page.index("v7.0"), page.index("v6.4.20"))            # новые сверху
        self.assertNotIn("<script>", siteversions.render({"7.0": ["<script>x</script>"]}, "7.0"))   # текст экранируется


if __name__ == "__main__":
    unittest.main()
