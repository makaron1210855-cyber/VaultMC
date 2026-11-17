"""Проверка сети: логика выводов на подставных ответах (без настоящего интернета) + один живой прогон."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import netcheck  # noqa: E402

SITES = [("Mojang: версии игры", "https://piston-meta.mojang.com/x", "игра"),
         ("Ely.by: вход и скины", "https://authserver.ely.by/", "вход"),
         ("Сервер VaultMC", "https://vaultmc-skins.vaultmc.workers.dev/api/status", "друзья")]


def run(http, doh=lambda h: False, relay=lambda: (True, 50, ""), speed=lambda: (80.0, ""), sites=SITES):
    return netcheck.run(http=lambda url, timeout=8: http(url), doh=doh, relay=relay, speed=speed, sites=sites)


def by_name(res, name):
    return next(i for i in res["items"] if i["name"] == name)


class NetCheckTests(unittest.TestCase):
    def test_everything_fine(self):
        r = run(lambda u: (200, 120, "", False))
        self.assertEqual(r["level"], "ok")
        self.assertTrue(all(i["status"] == "ok" for i in r["items"]))

    def test_http_error_code_still_means_reachable(self):
        r = run(lambda u: (403, 90, "", False))
        self.assertEqual(r["level"], "ok")

    def test_slow_site_is_warning(self):
        r = run(lambda u: (200, 2600, "", False) if "ely" in u else (200, 100, "", False))
        self.assertEqual(r["level"], "warn")
        self.assertEqual(by_name(r, "Ely.by: вход и скины")["status"], "slow")

    def test_provider_dns_block_is_recognized(self):
        def http(u):
            return (None, 30, "не находится адрес (DNS)", True) if "ely" in u else (200, 100, "", False)
        r = run(http, doh=lambda host: "ely" in host)
        item = by_name(r, "Ely.by: вход и скины")
        self.assertEqual(item["status"], "fail")
        self.assertIn("1.1.1.1", item["hint"])
        self.assertIn("Ely.by", r["summary"])

    def test_dns_error_but_doh_also_fails_is_generic_block(self):
        r = run(lambda u: (None, 30, "таймаут", False) if "ely" in u else (200, 100, "", False))
        self.assertIn("Ely.by", by_name(r, "Ely.by: вход и скины")["hint"])
        self.assertEqual(r["level"], "bad")

    def test_no_internet_at_all(self):
        r = run(lambda u: (None, 3000, "таймаут", False), relay=lambda: (False, 3000, "таймаут"), speed=lambda: (0.0, "нет сети"))
        self.assertEqual(r["level"], "bad")
        self.assertIn("Интернета нет", r["summary"])

    def test_relay_down_explained(self):
        r = run(lambda u: (200, 100, "", False), relay=lambda: (False, 6000, "таймаут"))
        item = by_name(r, "Ретранслятор VaultMC")
        self.assertEqual(item["status"], "fail")
        self.assertIn("e4mc", item["hint"])

    def test_slow_speed_estimates_download_time(self):
        r = run(lambda u: (200, 100, "", False), speed=lambda: (4.0, ""))
        item = by_name(r, "Скорость скачивания")
        self.assertEqual(item["status"], "slow")
        self.assertIn("мин", item["hint"])

    def test_text_for_report(self):
        r = run(lambda u: (None, 30, "таймаут", False) if "mojang" in u else (200, 100, "", False))
        self.assertIn("НЕТ  Mojang", r["text"])
        self.assertIn("→", r["text"])

    def test_live_run_has_all_rows(self):
        """Настоящий прогон: не проверяем результат (зависит от сети), только что ничего не падает и строки на месте."""
        try:
            r = netcheck.run()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"живой прогон упал: {exc}")
        self.assertEqual(len(r["items"]), len(netcheck.SITES) + 2)
        self.assertIn(r["level"], ("ok", "warn", "bad"))


if __name__ == "__main__":
    unittest.main()
