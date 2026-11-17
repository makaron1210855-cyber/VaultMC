"""Отчёт для поддержки: личное вырезается, секреты в архив не попадают."""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import supportreport as sr  # noqa: E402


class RedactTests(unittest.TestCase):
    def test_windows_user_in_paths(self):
        text = r"Loading C:\Users\Ivan Petrov\.vaultmc\shared\libraries\x.jar and C:/Users/ivan/AppData and /home/ivan/.minecraft"
        out = sr.redact(text)
        self.assertNotIn("Petrov", out)
        self.assertNotIn("/ivan/", out)
        self.assertIn(r"C:\Users\<пользователь>\.vaultmc", out)
        self.assertIn("C:/Users/<пользователь>/AppData", out)

    def test_path_without_following_slash_does_not_swallow_text(self):
        out = sr.redact("user dir is /home/ivan, then /etc/x and C:\\Users\\Bob")
        self.assertIn("then /etc/x", out)
        self.assertNotIn("ivan", out)
        self.assertNotIn("Bob", out)

    def test_names_everywhere(self):
        out = sr.redact("Setting user: Marsonchek\nHello MARSONCHEK!", names=("Marsonchek",))
        self.assertNotIn("arsonchek".lower(), out.lower().replace("<имя>", ""))
        self.assertEqual(out.count("<имя>"), 2)

    def test_name_inside_other_word_is_kept(self):
        self.assertEqual(sr.redact("Marsonchekov is fine", names=("Marsonchek",)), "Marsonchekov is fine")

    def test_short_names_ignored(self):
        self.assertEqual(sr.redact("ab at it", names=("ab",)), "ab at it")

    def test_email(self):
        self.assertEqual(sr.redact("contact: someone.name+mc@example.co.uk now"), "contact: <почта> now")

    def test_public_ip_removed_local_kept_versions_kept(self):
        out = sr.redact("connect to 13.143.207.131:25601, local 127.0.0.1:25565, mod fabric-api-0.92.2.1+1.20.1 and v1.2.3.4-beta")
        self.assertIn("<ip>:25601", out)
        self.assertIn("127.0.0.1:25565", out)
        self.assertIn("fabric-api-0.92.2.1+1.20.1", out)
        self.assertIn("v1.2.3.4-beta", out)

    def test_launch_command_tokens(self):
        cmd = "java -cp x net.minecraft.client.main.Main --username Steve --accessToken eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4 --uuid abc --clientId 12345"
        out = sr.redact(cmd)
        self.assertNotIn("eyJhbGci", out)
        self.assertNotIn("12345", out)
        self.assertIn("--accessToken <скрыто>", out)

    def test_bearer_and_key_value_secrets(self):
        out = sr.redact('Authorization: Bearer abc.def-123\n"access_token": "sekret123"\npassword=hunter2 token: zzz')
        for leak in ("abc.def-123", "sekret123", "hunter2", "zzz"):
            self.assertNotIn(leak, out)

    def test_long_hex_and_curseforge_key(self):
        key64 = "a1b2c3d4" * 8
        out = sr.redact(f"key {key64} cf $2a$10$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQR")
        self.assertNotIn(key64, out)
        self.assertNotIn("$2a$10$", out)

    def test_stats_counted(self):
        stats = {}
        sr.redact("a@b.co c@d.co 8.8.8.8", stats=stats)
        self.assertEqual((stats["почта"], stats["ip"]), (2, 1))


class CollectTests(unittest.TestCase):
    def test_collect_has_no_secret_files_and_cleans_everything(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            game, home = root / "minecraft", root / "home"
            (game / "logs").mkdir(parents=True)
            (game / "crash-reports").mkdir()
            home.mkdir()
            (home / "launcher.log").write_text(r"старт C:\Users\Secret\app token=TOPSECRET" + "\n", encoding="utf-8")
            (home / "accounts.json").write_text("PASSWORDHASH", encoding="utf-8")
            (home / "skinserver.json").write_text("SERVERKEY", encoding="utf-8")
            (game / "logs" / "latest.log").write_text("[10:00:00] [main/INFO]: Setting user: Nick123\n", encoding="utf-8")
            (game / "crash-reports" / "crash-2026-11-17_10.00.00-client.txt").write_text("Description: boom\n", encoding="utf-8")
            files, stats = sr.collect(game, home, {"launcher": "6.4.19", "sections": [("ПК", ["RAM 16"])]},
                                      console=["строка"], names=("Nick123",), net_text="Mojang: ok")
            blob = "\n".join(files.values())
            for leak in ("TOPSECRET", "Secret", "PASSWORDHASH", "SERVERKEY", "Nick123"):
                self.assertNotIn(leak, blob)
            self.assertIn("launcher.log", files)
            self.assertIn("report.txt", files)
            self.assertIn("network-check.txt", files)
            self.assertTrue(any(n.startswith("crash-reports/") for n in files))
            self.assertNotIn("accounts.json", files)
            self.assertGreaterEqual(stats.get("токены", 0), 1)

    def test_zip_roundtrip(self):
        data = sr.build_zip({"a.txt": "один", "crash-reports/b.txt": "два"})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(sorted(z.namelist()), ["a.txt", "crash-reports/b.txt"])
            self.assertEqual(z.read("a.txt").decode("utf-8"), "один")

    def test_big_log_is_trimmed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "big.log"
            p.write_text("x" * (sr.TAIL_GAME + 5000), encoding="utf-8")
            self.assertLess(len(sr._tail(p, sr.TAIL_GAME)), sr.TAIL_GAME + 100)


if __name__ == "__main__":
    unittest.main()
