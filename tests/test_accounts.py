"""Аккаунты: код восстановления, сброс пароля, смена ника, закрытие старых ключей при смене пароля."""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import accounts as A  # noqa: E402
from core import skinserver as ss  # noqa: E402


class LocalAccountTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = A.AccountStore(Path(self._tmp.name) / "accounts.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reset_password_replaces_local_password(self):
        acc = self.store.register_offline("Steve_1", "oldpass1", "oldpass1")
        self.store.reset_offline_password("steve_1", "newpass2", "newpass2")
        with self.assertRaises(A.AuthError):
            self.store.login_offline("Steve_1", "oldpass1")
        self.assertEqual(self.store.login_offline("Steve_1", "newpass2").id, acc.id)

    def test_reset_password_on_new_pc_creates_account(self):
        acc = self.store.reset_offline_password("Fresh_PC", "newpass2", "newpass2")
        self.assertEqual(acc.username, "Fresh_PC")
        self.assertEqual(self.store.active.id, acc.id)

    def test_rename_changes_nick_and_uuid(self):
        acc = self.store.register_offline("Old_Nick", "pass123", "pass123")
        old_uuid = acc.uuid
        self.store.rename_offline(acc.id, "New_Nick")
        self.assertEqual(acc.username, "New_Nick")
        self.assertNotEqual(acc.uuid, old_uuid)
        self.assertEqual(self.store.login_offline("New_Nick", "pass123").id, acc.id, "пароль прежний")

    def test_rename_refuses_existing_local_nick(self):
        a = self.store.register_offline("Nick_A", "pass123", "pass123")
        self.store.register_offline("Nick_B", "pass123", "pass123")
        with self.assertRaises(A.AuthError):
            self.store.rename_offline(a.id, "nick_b")

    def test_check_password(self):
        acc = self.store.register_offline("Steve_1", "pass123", "pass123")
        self.store.check_password(acc.id, "pass123")
        with self.assertRaises(A.AuthError):
            self.store.check_password(acc.id, "wrong!!")


class SkinServerClientTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(ss, "TOKENS", Path(self._tmp.name) / "skinserver.json"),
                        mock.patch.object(ss, "server_url", lambda: "https://x.test")]
        for p in self.patches:
            p.start()
        ss._registered.clear()
        ss._active.clear()
        self.calls = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    def fake_call(self, path, payload):
        self.calls.append((path, dict(payload)))
        return {"ok": True}

    def test_recovery_code_format(self):
        code = ss.make_recovery_code()
        self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}$")
        self.assertNotEqual(code, ss.make_recovery_code())

    def test_password_change_sends_replace_once(self):
        with mock.patch.object(ss, "_call", self.fake_call):
            ss.set_password("Steve_1", "oldpass1")
            ss.ensure_registered("Steve_1")
            ss.set_password("Steve_1", "newpass2", replace=True)
            ss.ensure_registered("Steve_1")
            ss._registered.clear()
            ss.ensure_registered("Steve_1")
        change = self.calls[1][1]
        self.assertTrue(change.get("replace"))
        self.assertEqual(change["old"], ss.password_token("Steve_1", "oldpass1"))
        self.assertEqual(change["token"], ss.password_token("Steve_1", "newpass2"))
        self.assertNotIn("replace", self.calls[2][1], "после успеха флаг снят")

    def test_login_on_other_pc_does_not_replace(self):
        with mock.patch.object(ss, "_call", self.fake_call):
            ss.set_password("Steve_1", "pass123")
            ss.ensure_registered("Steve_1")
        self.assertNotIn("replace", self.calls[0][1])

    def test_rename_moves_local_key(self):
        with mock.patch.object(ss, "_call", self.fake_call):
            ss.set_password("Old_Nick", "pass123")
            ss.rename("Old_Nick", "New_Nick", "pass123")
        path, payload = self.calls[-1]
        self.assertEqual(path, "/api/rename")
        self.assertEqual(payload["new"], "New_Nick")
        self.assertEqual(payload["newToken"], ss.password_token("New_Nick", "pass123"))
        data = ss._tokens()
        self.assertNotIn("old_nick", data)
        self.assertIn("new_nick", data)

    def test_recover_stores_new_key(self):
        with mock.patch.object(ss, "_call", self.fake_call):
            ss.recover("Steve_1", "ABCD-EFGH-JKLM-NPQR", "newpass2")
        path, payload = self.calls[-1]
        self.assertEqual(path, "/api/register")
        self.assertEqual(payload["recovery"], "ABCD-EFGH-JKLM-NPQR")
        self.assertEqual(ss._token_for("Steve_1"), ss.password_token("Steve_1", "newpass2"))


if __name__ == "__main__":
    unittest.main()
