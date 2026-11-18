import io
import json
import unittest
from unittest import mock

from core import discordrpc


class FakePipe(io.BytesIO):
    """Ответы Discord: на каждый запрос — пустой ответ рукопожатия и OK для SET_ACTIVITY."""

    def __init__(self, replies):
        super().__init__()
        self._stream = b"".join(replies)
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        return len(data)

    def read(self, n=-1):
        chunk, self._stream = self._stream[:n], self._stream[n:]
        return chunk

    def close(self):
        pass


def frame(opcode, payload):
    body = json.dumps(payload).encode("utf-8")
    return discordrpc.HEADER.pack(opcode, len(body)) + body


class DiscordRpcTests(unittest.TestCase):
    def test_frame_layout_and_payload(self):
        pipe = FakePipe([frame(1, {"cmd": "DISPATCH"}), frame(1, {"evt": None, "data": {}})])
        with mock.patch.object(discordrpc, "_open_pipe", return_value=pipe), \
                mock.patch.object(discordrpc, "is_windows", return_value=True):
            ok = discordrpc._push({"details": "Играет в VaultMC"})
        self.assertTrue(ok)
        opcode, length = discordrpc.HEADER.unpack(pipe.sent[0][:8])
        self.assertEqual(opcode, discordrpc.OP_HANDSHAKE)
        handshake = json.loads(pipe.sent[0][8:].decode())
        self.assertEqual(handshake, {"v": 1, "client_id": discordrpc.CLIENT_ID})
        opcode, _ = discordrpc.HEADER.unpack(pipe.sent[1][:8])
        self.assertEqual(opcode, discordrpc.OP_FRAME)
        cmd = json.loads(pipe.sent[1][8:].decode())
        self.assertEqual(cmd["cmd"], "SET_ACTIVITY")
        self.assertEqual(cmd["args"]["activity"], {"details": "Играет в VaultMC"})

    def test_no_discord_is_silent(self):
        with mock.patch.object(discordrpc, "_open_pipe", return_value=None), \
                mock.patch.object(discordrpc, "is_windows", return_value=True):
            self.assertFalse(discordrpc._push({"details": "x"}))

    def test_not_windows_does_nothing(self):
        with mock.patch.object(discordrpc, "_open_pipe") as opener, \
                mock.patch.object(discordrpc, "is_windows", return_value=False):
            self.assertFalse(discordrpc._push(None))
        opener.assert_not_called()

    def test_state_is_limited_to_discord_length(self):
        captured = {}
        with mock.patch.object(discordrpc, "_run_async", side_effect=lambda a: captured.update(a=a)):
            discordrpc.show_playing("я" * 300, "1.20.1", 1700000000.5)
        self.assertLessEqual(len(captured["a"]["state"]), 128)
        self.assertEqual(captured["a"]["timestamps"], {"start": 1700000000})


if __name__ == "__main__":
    unittest.main()
