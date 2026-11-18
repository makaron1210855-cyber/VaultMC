"""Ретранслятор VaultMC: сервер (server/relay/relay.py) и клиент лаунчера (core/relay.py) на этом ПК.
«Мир» хозяина — эхо-сервер: что гость отправил, то и вернулось через ретранслятор."""
import asyncio
import importlib.util
import socket
import socketserver
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import relay as client  # noqa: E402

spec = importlib.util.spec_from_file_location("relay_server", ROOT / "server" / "relay" / "relay.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def free_ports(n):
    """n свободных портов подряд (для диапазона комнат)."""
    for base in range(42000, 60000, 37):
        socks = []
        try:
            for p in range(base, base + n + 1):
                s = socket.socket()
                s.bind(("127.0.0.1", p))
                socks.append(s)
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("нет свободных портов")


BASE = free_ports(6)
srv.CONTROL_PORT, srv.PORT_MIN, srv.PORT_MAX = BASE, BASE + 1, BASE + 6
srv.PING_EVERY = 0.5            # мёртвых хозяев сервер замечает быстро
srv.log = lambda text: None
client.RELAY_HOST, client.CONTROL_PORT = "127.0.0.1", BASE

LOOP = asyncio.new_event_loop()
threading.Thread(target=lambda: (asyncio.set_event_loop(LOOP), LOOP.run_until_complete(srv.main())), daemon=True).start()
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", BASE), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.05)


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


ECHO = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
ECHO.daemon_threads = True
threading.Thread(target=ECHO.serve_forever, daemon=True).start()
ECHO_PORT = ECHO.server_address[1]


def on_loop(fn):
    async def run():
        return fn()
    return asyncio.run_coroutine_threadsafe(run(), LOOP).result(5)


def reset_server():
    def clear():
        for room in list(srv.rooms.values()):
            srv.close(room.writer)
        srv.rooms.clear()
        srv.joins.clear()
    on_loop(clear)


def roundtrip(port, payload=b"hello-vaultmc"):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(payload)
    got = b""
    while len(got) < len(payload):
        chunk = s.recv(65536)
        if not chunk:
            break
        got += chunk
    s.close()
    return got


def line(cmd):
    s = socket.create_connection(("127.0.0.1", BASE), timeout=5)
    s.sendall((cmd + "\n").encode())
    return s, client._line(s)


class RelayTests(unittest.TestCase):
    def setUp(self):
        reset_server()
        self.hosts, self.guests = [], []

    def tearDown(self):
        for h in self.hosts:
            h.stop()
        for g in self.guests:
            g.stop()

    def host(self, **kw):
        h = client.RelayHost(ECHO_PORT, lambda m: None, lambda p: None, **kw)
        self.hosts.append(h)
        h.start()
        return h

    def guest(self, port, token):
        g = client.RelayGuest(port, token, lambda m: None)
        self.guests.append(g)
        return g

    def test_guest_with_key_reaches_world(self):
        h = self.host()
        self.assertTrue(h.secure)
        g = self.guest(h.public_port, h.token)
        g.check()
        local = g.start()
        self.assertEqual(roundtrip(local), b"hello-vaultmc")
        big = bytes(range(256)) * 4000        # ~1 МБ — данные не теряются и не путаются
        self.assertEqual(roundtrip(local, big), big)
        time.sleep(0.2)
        self.assertGreater(g.rx, 0)
        self.assertGreater(g.tx, 0)

    def test_wrong_key_is_refused(self):
        h = self.host()
        g = self.guest(h.public_port, "0" * 32)
        with self.assertRaises(client.RelayError) as err:
            g.check()
        self.assertEqual(err.exception.reason, "key")

    def test_closed_room_reason_gone(self):
        g = self.guest(srv.PORT_MIN, "a" * 32)
        with self.assertRaises(client.RelayError) as err:
            g.check()
        self.assertEqual(err.exception.reason, "gone")

    def test_public_port_does_not_open_keyed_room(self):
        h = self.host()
        s = socket.create_connection(("127.0.0.1", h.public_port), timeout=5)
        s.sendall(b"\x10\x00stranger")
        s.settimeout(3)
        self.assertEqual(s.recv(100), b"", "посторонний без ключа не должен попасть в мир")
        s.close()

    def test_legacy_room_without_key_still_works(self):
        h = client.RelayHost(ECHO_PORT, lambda m: None, lambda p: None)
        h.secure = False                      # так открывает комнату лаунчер до 6.4.14
        self.hosts.append(h)
        h.start()
        self.assertEqual(roundtrip(h.public_port), b"hello-vaultmc")

    def test_reconnect_keeps_port(self):
        h = self.host()
        port = h.public_port
        h._ctl.close()                        # обрыв связи хозяина с ретранслятором
        deadline = time.time() + 10
        while time.time() < deadline and (h._ctl is None or h._ctl.fileno() == -1):
            time.sleep(0.1)
        time.sleep(0.5)
        self.assertEqual(h.public_port, port)
        g = self.guest(port, h.token)
        g.check()
        self.assertEqual(roundtrip(g.start()), b"hello-vaultmc")

    def test_old_relay_falls_back_to_room_without_key(self):
        class Old(socketserver.BaseRequestHandler):   # ретранслятор до 6.4.14: HOST2 не знает и закрывает
            def handle(self):
                f = self.request.makefile("rb")
                if f.readline().strip() == b"HOST":
                    self.request.sendall(b"PORT 25777\n")
                    time.sleep(2)
        old = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Old)
        old.daemon_threads = True
        threading.Thread(target=old.serve_forever, daemon=True).start()
        saved = client.CONTROL_PORT
        client.CONTROL_PORT = old.server_address[1]
        try:
            h = client.RelayHost(ECHO_PORT, lambda m: None, lambda p: None)
            self.hosts.append(h)
            self.assertEqual(h.start(), 25777)
            self.assertFalse(h.secure)
        finally:
            client.CONTROL_PORT = saved
            old.shutdown()

    def test_rooms_per_address_limited(self):
        socks = []
        for _ in range(srv.HOSTS_PER_IP):
            s, answer = line("HOST2 " + "b" * 32)
            self.assertTrue(answer.startswith("PORT "))
            socks.append(s)
        s, answer = line("HOST2 " + "c" * 32)
        self.assertEqual(answer, "ERR limit")
        for x in socks + [s]:
            x.close()

    def test_join_attempts_limited(self):
        answers = []
        for _ in range(srv.JOINS_PER_MIN + 1):
            s, a = line(f"CHECK {srv.PORT_MIN} {'d' * 32}")
            s.close()
            answers.append(a)
        self.assertEqual(answers[-1], "ERR limit")
        self.assertEqual(answers[0], "ERR gone")

    def test_ping(self):
        s, answer = line("PING")
        s.close()
        self.assertEqual(answer, "PONG")

    def test_ping_best_measures_relay(self):
        ms = client.ping_best(tries=3)
        self.assertIsNotNone(ms)
        self.assertGreaterEqual(ms, 0)
        self.assertLess(ms, 1000)

    def test_ping_best_none_when_relay_down(self):
        saved = client.CONTROL_PORT
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        client.CONTROL_PORT = probe.getsockname()[1]   # порт занят, но никто не слушает — соединение отклонят
        try:
            self.assertIsNone(client.ping_best(tries=2, timeout=1))
        finally:
            probe.close()
            client.CONTROL_PORT = saved


if __name__ == "__main__":
    unittest.main()


class GuestLimitTests(unittest.TestCase):
    def setUp(self):
        reset_server()
        self.hosts = []

    def tearDown(self):
        for h in self.hosts:
            h.stop()

    def test_room_refuses_guest_over_limit(self):
        h = client.RelayHost(ECHO_PORT, lambda m: None, lambda p: None)
        self.hosts.append(h)
        h.start()
        port = h.public_port
        on_loop(lambda: setattr(srv.rooms[port], "guests", srv.MAX_GUESTS))   # комната заполнена
        s, answer = line(f"JOIN {port} {h.token}")
        s.close()
        self.assertEqual(answer, "ERR busy")
        on_loop(lambda: setattr(srv.rooms[port], "guests", 0))
        self.assertEqual(srv.MAX_GUESTS, 10)
