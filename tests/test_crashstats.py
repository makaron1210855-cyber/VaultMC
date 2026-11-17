"""Анонимная статистика падений: что уходит на сервер и что — никогда."""
import json
import unittest
from unittest import mock

from core import crashstats


class CrashStatsTests(unittest.TestCase):
    def test_kinds_from_fixes(self):
        a = [{"title": "x", "fix": {"action": "ram_down"}}, {"title": "y", "fix": None}, {"title": "z", "fix": {"action": "ram_down"}}]
        self.assertEqual(crashstats.kinds_of(a), ["ram_down", "other"])
        self.assertEqual(crashstats.kinds_of([]), ["unknown"])
        self.assertEqual(crashstats.kinds_of([{"fix": {"action": "C:/Users/Иван"}}]), ["other"])

    def test_report_has_only_safe_fields(self):
        r = crashstats.build("oom", "6.4.21", "1.20.1", "Fabric")
        self.assertEqual(set(r), {"kind", "app", "mc", "loader"})
        self.assertEqual(r["loader"], "fabric")
        dirty = crashstats.build("oom", "6.4.21", "C:/Users/Иван/mc", "fabric/../x")
        self.assertNotIn("Иван", json.dumps(dirty, ensure_ascii=False))
        self.assertNotIn("/", dirty["mc"])

    def test_no_network_error_is_silent(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertFalse(crashstats._post({"kind": "oom", "app": "6.4.21", "mc": "", "loader": ""}))


if __name__ == "__main__":
    unittest.main()
