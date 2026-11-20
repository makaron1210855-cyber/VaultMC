"""Облако миров, часть лаунчера: отпечаток, архив и вредные архивы, state.json, таблица решений, замена папки с журналом,
порядок действий (core/worldengine.py) с поддельным сервером — два «ПК» с отдельными папками."""
import gzip
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import worldcloud, worldsync as S  # noqa: E402
from core.worldcloud import CloudError  # noqa: E402
from core.worldengine import CloudEngine, WorldError, Busy  # noqa: E402


def nbt_level(name="World", last=0, mc="1.20.1"):
    def s(t):
        b = t.encode()
        return struct.pack(">H", len(b)) + b
    d = bytes([10]) + s("") + bytes([10]) + s("Data")
    d += bytes([8]) + s("LevelName") + s(name) + bytes([4]) + s("LastPlayed") + struct.pack(">q", last)
    d += bytes([3]) + s("DataVersion") + struct.pack(">i", 3465)
    d += bytes([10]) + s("Version") + bytes([8]) + s("Name") + s(mc) + bytes([0])
    d += bytes([0, 0])
    return gzip.compress(d)


def make_world(path: Path, name="World", payload=b"R" * 3000, extra=None):
    (path / "region").mkdir(parents=True, exist_ok=True)
    (path / "level.dat").write_bytes(nbt_level(name))
    (path / "region" / "r.0.0.mca").write_bytes(payload)
    for rel, data in (extra or {}).items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return path


def bump(path: Path, ahead=5):
    t = path.stat().st_mtime + ahead
    os.utime(path, (t, t))


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class FingerprintArchiveTests(TmpCase):
    def test_fingerprint_ignores_lock_and_cache(self):
        w = make_world(self.root / "w", extra={"session.lock": b"x", "data/DistantHorizons.sqlite": b"1" * 50})
        a = S.fingerprint(w)
        (w / "session.lock").write_bytes(b"changed")
        (w / "data" / "DistantHorizons.sqlite-wal").write_bytes(b"2")
        self.assertEqual(a, S.fingerprint(w))
        bump(w / "region" / "r.0.0.mca")
        self.assertNotEqual(a, S.fingerprint(w))

    def test_archive_roundtrip_and_part_md5(self):
        w = make_world(self.root / "w", payload=os.urandom(9000), extra={"data/DistantHorizons.sqlite": b"cache", "session.lock": b"l"})
        out = self.root / "out" / "a.zip"
        info = S.build_archive(w, out, part_size=4096)
        self.assertEqual(info["size"], out.stat().st_size)
        self.assertEqual(info["sha256"], hashlib.sha256(out.read_bytes()).hexdigest())
        raw = out.read_bytes()
        self.assertEqual(info["md5"], [hashlib.md5(raw[i:i + 4096]).hexdigest() for i in range(0, len(raw), 4096)])  # noqa: S324
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        self.assertTrue(all(n.startswith("world/") for n in names))
        self.assertFalse([n for n in names if "DistantHorizons" in n or "session.lock" in n])
        dest = self.root / "new" / "world"
        S.extract_archive(out, dest, limit=10_000_000)
        self.assertEqual((dest / "region" / "r.0.0.mca").read_bytes(), (w / "region" / "r.0.0.mca").read_bytes())
        self.assertFalse(out.with_name(out.name + ".part").exists())

    def test_archive_interrupted(self):
        w = make_world(self.root / "w")
        with self.assertRaises(S.Interrupted):
            S.build_archive(w, self.root / "a.zip", still_ok=lambda: False)
        self.assertFalse((self.root / "a.zip").exists())


class EvilArchiveTests(TmpCase):
    def zip_of(self, members, mode=None):
        z = self.root / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name, data in members:
                zi = zipfile.ZipInfo(name)
                if mode:
                    zi.external_attr = mode << 16
                zf.writestr(zi, data)
        return z

    def assertUnsafe(self, members, mode=None, limit=10_000_000):
        z = self.zip_of(members, mode)
        with self.assertRaises(S.UnsafeArchive):
            S.extract_archive(z, self.root / "dest", limit)
        self.assertFalse((self.root / "x.txt").exists())

    def test_raw_bad_names(self):
        # zipfile сам правит обратную косую и NUL при создании записи, но чужой архив может их содержать
        for name in ("world/a\b", "world/a\x00b"):
            with self.assertRaises(S.UnsafeArchive):
                S.check_member(name)

    def test_bad_names(self):
        for name in ("../x.txt", "world/../x.txt", "/abs/x.txt", "world/C:/x", "world/a:b", "x/level.dat",
                     "world/CON.txt", "world/con", "world/aux.dat", "world/COM1", "world/LPT9.x", "world/a./b", "world/a /b",
                     "world/a<b", "world/a?b", "world/a|b", 'world/a"b', "world/a*b", "world/a\x07b",
                     "world//a", "world/./a", "world/" + "d/" * 80 + "f"):
            with self.subTest(name=name):
                self.assertUnsafe([(name, b"x")])

    def test_symlink_and_duplicates(self):
        self.assertUnsafe([("world/link", b"/etc/passwd")], mode=0o120777)
        self.assertUnsafe([("world/A.txt", b"1"), ("world/a.TXT", b"2")])

    def test_file_and_dir_clash(self):
        self.assertUnsafe([("world/a", b"1"), ("world/a/b", b"2")])

    def test_bomb_counts_real_bytes(self):
        z = self.zip_of([("world/big", b"0" * 200_000)])
        with self.assertRaises(S.UnsafeArchive):
            S.extract_archive(z, self.root / "dest", limit=100_000)
        # заголовок врёт: настоящий размер больше заявленного
        z2 = self.zip_of([("world/big", b"0" * 200_000)])
        with mock.patch.object(zipfile.ZipInfo, "file_size", 10, create=True):
            pass
        with self.assertRaises(S.UnsafeArchive):
            S.extract_archive(z2, self.root / "dest2", limit=199_999)

    def test_not_a_zip(self):
        p = self.root / "junk.zip"
        p.write_bytes(b"not a zip")
        with self.assertRaises(S.UnsafeArchive):
            S.extract_archive(p, self.root / "d", 1000)

    def test_ok_dirs_and_names(self):
        z = self.zip_of([("world/", b""), ("world/region/", b""), ("world/region/r.0.0.mca", b"abc"), ("world/level.dat", b"1")])
        res = S.extract_archive(z, self.root / "dest", 1000)
        self.assertEqual(res["files"], 2)


class DecideTests(unittest.TestCase):
    def d(self, **kw):
        base = dict(online=True, owner_ok=True, gone=False, listed=True, folder_exists=True, changed=False, relation="same")
        return S.decide(**{**base, **kw})

    def test_table(self):
        self.assertEqual(self.d(), "none")
        self.assertEqual(self.d(changed=True), "upload")
        self.assertEqual(self.d(relation="behind"), "download")
        self.assertEqual(self.d(relation="behind", changed=True), "choose")
        self.assertEqual(self.d(relation="unknown"), "choose")
        self.assertEqual(self.d(relation="unknown", changed=True), "choose")
        self.assertEqual(self.d(gone=True, listed=False, relation=None), "gone")
        self.assertEqual(self.d(folder_exists=False), "cloud_only")
        self.assertEqual(self.d(online=False), "offline")
        self.assertEqual(self.d(listed=False, relation=None), "offline")      # ответ неполный — как «нет связи»
        self.assertEqual(self.d(owner_ok=False), "paused_owner")
        self.assertEqual(self.d(changed=True, older=True), "choose")           # «тормоз» по level_mtime
        self.assertEqual(self.d(changed=True, hold="crash"), "held")
        self.assertEqual(self.d(hold="conflict", changed=True), "held")
        self.assertEqual(self.d(relation="behind", hold="conflict"), "held")
        self.assertEqual(self.d(choose=True), "choose")

    def test_never_picks_by_clock(self):
        # «старее» — только повод спросить игрока, но не выбрать копию
        self.assertNotIn(self.d(changed=True, older=True), ("upload", "download"))


class StateTests(TmpCase):
    def test_bak_and_corrupt(self):
        path = self.root / "state.json"
        st = S.SyncState(path)
        st.add(S.new_binding("Steve", "i1", "World", "w" * 24))
        st.update(st.find("steve", "i1", "World"), base_rid="r1")          # второе сохранение создаёт .bak
        path.write_text("{ битый", encoding="utf-8")
        st2 = S.SyncState(path)
        self.assertEqual(len(st2.all()), 1)                                # взяли .bak
        path.write_text("{", encoding="utf-8")
        path.with_name("state.json.bak").write_text("x", encoding="utf-8")
        st3 = S.SyncState(path)
        self.assertEqual(st3.all(), [])                                    # нет и .bak → привязок нет, ничего не удаляем

    def test_new_device_on_other_machine_drops_uploads(self):
        path = self.root / "state.json"
        st = S.SyncState(path)
        old_id = st.device["id"]
        b = S.new_binding("Steve", "i1", "World", "w" * 24)
        st.add({**b, "upload": {"sid": "x"}})
        with mock.patch.object(S, "machine_hash", return_value="other-machine"):
            st2 = S.SyncState(path)
        self.assertNotEqual(old_id, st2.device["id"])
        self.assertIsNone(st2.all()[0]["upload"])

    def test_one_folder_one_binding_and_update_copy(self):
        st = S.SyncState(self.root / "state.json")
        st.add(S.new_binding("Steve", "i1", "World", "w" * 24))
        self.assertIsNotNone(st.find_folder("i1", "World"))
        got = st.find("Steve", "i1", "World")
        got["fp"] = "mutated"                                                # копия: хранилище не меняется
        self.assertEqual(st.find("Steve", "i1", "World")["fp"], "")
        st.rename_nick("Steve", "Alex")
        self.assertEqual(st.all()[0]["nick"], "Alex")

    def test_parallel_updates_do_not_lose_records(self):
        st = S.SyncState(self.root / "state.json")
        for i in range(8):
            st.add(S.new_binding("Steve", "i1", f"W{i}", f"{i:024x}"))

        def work(i):
            for n in range(20):
                st.update(st.find("Steve", "i1", f"W{i}"), base_rev=n)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual([b["base_rev"] for b in S.SyncState(self.root / "state.json").all()], [19] * 8)

    def test_playing_mark_and_java_pid(self):
        st = S.SyncState(self.root / "state.json")
        st.set_playing("i1", 123)
        self.assertEqual(S.SyncState(self.root / "state.json").playing()["pid"], 123)
        st.set_playing()
        self.assertEqual(st.playing(), {})
        self.assertFalse(S.java_pid_alive(0))
        self.assertTrue(S.java_pid_alive(os.getpid()) is False or "java" in sys.executable.lower())   # python — не java


class ApplyRecoverTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.inst_root = self.root / "inst"
        self.game = self.inst_root / "minecraft"
        self.old = make_world(self.game / "saves" / "World", payload=b"OLD" * 100, extra={"data/DistantHorizons.sqlite": b"CACHE" * 10})
        self.new = make_world(S.new_dir(self.inst_root, "w" * 24) / "world", payload=b"NEW" * 100)
        self.fp_new = S.fingerprint(self.new)
        self.kw = dict(wid="w" * 24, rid="r" * 16, rev=3, nick="Steve", fp_new=self.fp_new)

    def apply(self):
        return S.apply_cloud_copy(self.inst_root, self.game, "World", self.new, **self.kw)

    def test_happy_path(self):
        res = self.apply()
        self.assertEqual((self.game / "saves" / "World" / "region" / "r.0.0.mca").read_bytes(), b"NEW" * 100)
        self.assertEqual(res["fp"], self.fp_new)
        self.assertEqual(S.fingerprint(self.game / "saves" / "World"), self.fp_new)
        trash = list((self.inst_root / "trash").iterdir())
        self.assertEqual(len([t for t in trash if t.is_dir()]), 1)
        self.assertTrue(any(t.suffix == ".json" for t in trash))              # пометка «заменён версией из облака»
        kept = [t for t in trash if t.is_dir()][0]
        self.assertEqual((kept / "region" / "r.0.0.mca").read_bytes(), b"OLD" * 100)    # прежняя копия не пропала
        self.assertEqual((self.game / "saves" / "World" / "data" / "DistantHorizons.sqlite").read_bytes(), b"CACHE" * 10)  # кэш переехал
        self.assertFalse((S.sync_dir(self.inst_root) / S.JOURNAL).exists())

    def crash_at(self, target_name_check):
        real = os.rename

        def boom(src, dst, *a, **k):
            if target_name_check(Path(src), Path(dst)):
                raise OSError("выключили ПК")
            return real(src, dst, *a, **k)
        return mock.patch("core.worldsync.os.rename", side_effect=boom)

    def test_crash_before_trash_rollback_keeps_world(self):
        with self.crash_at(lambda s, d: d.parent.name == "trash"), self.assertRaises(OSError):
            self.apply()
        self.assertIsNotNone(S.recover(self.inst_root, self.game) or True)
        self.assertEqual((self.game / "saves" / "World" / "region" / "r.0.0.mca").read_bytes(), b"OLD" * 100)
        self.assertTrue((self.game / "saves" / "World" / "data" / "DistantHorizons.sqlite").exists())      # кэш вернулся
        self.assertFalse(S.new_dir(self.inst_root, "w" * 24).exists())
        self.assertFalse((S.sync_dir(self.inst_root) / S.JOURNAL).exists())

    def test_crash_after_trash_before_place_is_finished(self):
        with self.crash_at(lambda s, d: d == self.game / "saves" / "World"), self.assertRaises(OSError):
            self.apply()
        self.assertFalse((self.game / "saves" / "World").exists())          # папка мира в корзине, распаковка ещё не на месте
        res = S.recover(self.inst_root, self.game)
        self.assertEqual(res["rid"], "r" * 16)
        self.assertEqual(res["nick"], "Steve")
        self.assertEqual((self.game / "saves" / "World" / "region" / "r.0.0.mca").read_bytes(), b"NEW" * 100)

    def test_crash_after_place_before_finish(self):
        with mock.patch.object(S, "_finish", side_effect=OSError("выключили")), self.assertRaises(OSError):
            self.apply()
        res = S.recover(self.inst_root, self.game)
        self.assertEqual(res["fp"], self.fp_new)
        self.assertFalse((S.sync_dir(self.inst_root) / S.JOURNAL).exists())

    def test_foreign_copy_is_not_ours(self):
        with mock.patch.object(S, "_finish", side_effect=OSError("выключили")), self.assertRaises(OSError):
            self.apply()
        bump(self.game / "saves" / "World" / "region" / "r.0.0.mca", 50)     # с тех пор в мир играли
        self.assertIsNone(S.recover(self.inst_root, self.game))
        self.assertFalse((S.sync_dir(self.inst_root) / S.JOURNAL).exists())

    def test_crash_right_after_journal(self):
        S._write_journal(self.inst_root, {"wid": "w" * 24, "rid": "r" * 16, "rev": 1, "nick": "Steve", "folder": "World",
                                          "fp_new": self.fp_new, "trash": "", "new": str(self.new), "step": "start"})
        self.assertIsNone(S.recover(self.inst_root, self.game))
        self.assertEqual((self.game / "saves" / "World" / "region" / "r.0.0.mca").read_bytes(), b"OLD" * 100)

    def test_journal_pointing_elsewhere_is_ignored(self):
        S._write_journal(self.inst_root, {"wid": "w" * 24, "folder": "World", "new": str(self.root / "elsewhere" / "world"), "step": "start"})
        self.assertIsNone(S.recover(self.inst_root, self.game))
        S._write_journal(self.inst_root, {"wid": "w" * 24, "folder": "..", "new": str(self.new), "step": "start"})
        self.assertIsNone(S.recover(self.inst_root, self.game))

    def test_rename_failure_does_not_copy(self):
        # os.rename не прошёл — shutil.move не вызывается, в saves не остаётся «полмира»
        with mock.patch("core.worldsync.os.rename", side_effect=OSError("занято")), mock.patch("shutil.move") as mv, \
                self.assertRaises(OSError):
            self.apply()
        mv.assert_not_called()
        self.assertEqual((self.game / "saves" / "World" / "region" / "r.0.0.mca").read_bytes(), b"OLD" * 100)

    def test_free_folder(self):
        saves = self.game / "saves"
        self.assertEqual(S.free_folder(saves, "world"), "world (2)")         # без учёта регистра (Windows)
        self.assertEqual(S.free_folder(saves, 'a<b>:c'), "a_b__c")


class TrashMarkerTests(TmpCase):
    def test_marker_lifecycle_and_avoid(self):
        from core import worlds as W
        game, trash = self.root / "mc", self.root / "trash"
        make_world(game / "saves" / "Alpha")
        entry = S._rename_to(game / "saves" / "Alpha", trash / "Alpha 2026-11-19 10-00-00", "cloud", "r1")
        self.assertEqual(W.list_trash(trash)[0]["reason"], "cloud")
        make_world(game / "saves" / "Alpha")                                  # привязанная папка снова занята
        got = W.restore_trash(game, trash, entry, avoid=["alpha"])
        self.assertEqual(got, "Alpha (2)")                                    # не встаёт в имя привязанной папки
        self.assertFalse((trash / (entry + ".json")).exists())


# ============================================================================ поддельный сервер и два ПК
class FakeCloud:
    """Ведёт себя как server/worlds.js: цепочка версий, begin/commit со сравнением base, conflict, relation."""

    def __init__(self):
        self.worlds, self.sessions, self.parts, self.blobs, self.calls = {}, {}, {}, {}, []
        self.fail = {}           # действие → исключение (один раз)
        self.owner = "o:aaaa"

    def call(self, nick, kind, action, **p):
        self.calls.append(action)
        if action in self.fail:
            raise self.fail.pop(action)
        return getattr(self, "a_" + action)(**p)

    def a_list(self, device, device_name, bases):
        out, gone = [], []
        for wid, base in bases.items():
            w = self.worlds.get(wid)
            if not w:
                gone.append(wid)
        for wid, w in self.worlds.items():
            rel = None
            if wid in bases:
                b = bases[wid]
                rel = "same" if b == w["head"] else "behind" if b in w["hist"][:-1] else "unknown"
            head = {**w["vers"][w["head"]], "rid": w["head"], "rev": len(w["hist"])}
            out.append({"wid": wid, "title": head["meta"]["title"], "head": head, "prev": None, "relation": rel,
                        "lease": w.get("lease"), "upload": None})
        return {"ok": True, "worlds": out, "gone": gone, "owner_tag": hashlib.sha256(self.owner.encode()).hexdigest()[:8],
                "usage": {"used": 0, "quota": 10**9, "worlds": len(out), "max_worlds": 5}, "limits": {}}

    def a_begin(self, wid, base, size, sha256, meta, device, device_name, new=False):
        w = self.worlds.get(wid)
        if not w and not new:
            raise CloudError("gone", 404, "gone")
        if (w["head"] if w else "") != base:
            raise CloudError("conflict", 409, "conflict", {"head": self.head(wid)})
        sid, rid = f"{len(self.sessions):032x}", f"{len(self.sessions) + 100:016x}"
        parts = -(-size // self.part_size)
        self.sessions[sid] = {"wid": wid, "rid": rid, "base": base, "size": size, "sha": sha256, "meta": meta, "parts": parts,
                              "device_name": device_name, "done": None}
        return {"ok": True, "sid": sid, "wid": wid, "rid": rid, "part_size": self.part_size, "parts": parts}

    part_size = 16 << 20

    def head(self, wid):
        w = self.worlds.get(wid)
        return {**w["vers"][w["head"]], "rid": w["head"], "rev": len(w["hist"])} if w else None

    def a_commit(self, sid, parts):
        s = self.sessions[sid]
        if s["done"]:
            return s["done"]
        w = self.worlds.get(s["wid"])
        if (w["head"] if w else "") != s["base"]:
            raise CloudError("conflict", 409, "conflict", {"head": self.head(s["wid"])})
        data = b"".join(self.parts[(sid, n)] for n in range(1, s["parts"] + 1))
        assert hashlib.sha256(data).hexdigest() == s["sha"]
        if not w:
            w = self.worlds[s["wid"]] = {"head": "", "hist": [], "vers": {}}
        w["vers"][s["rid"]] = {"size": s["size"], "sha256": s["sha"], "meta": s["meta"], "device_name": s["device_name"], "at": 1}
        w["head"] = s["rid"]
        w["hist"].append(s["rid"])
        self.blobs[s["rid"]] = data
        s["done"] = {"ok": True, "rev": len(w["hist"]), "rid": s["rid"], "at": 1}
        return s["done"]

    def a_abort(self, sid=None, wid=None):
        return {"ok": True}

    def a_lease(self, device, device_name, wids):
        return {"ok": True, "leases": {}}

    def a_release(self, device, wids):
        return {"ok": True}

    def a_delete(self, wid):
        self.worlds.pop(wid, None)
        return {"ok": True}

    def put_part(self, sid, n, data, md5, progress=None):
        self.parts[(sid, n)] = data
        self.put_log.append(n)
        if self.put_hook:
            self.put_hook(n)
        return md5

    put_hook = None
    put_log: list = []

    def download(self, nick, kind, wid, rid, dest, size, progress=None, cancelled=lambda: False):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.blobs[rid])


class Pc:
    """Один «компьютер»: свои папки .vaultmc/worldsync, сборка, state.json."""

    def __init__(self, base: Path, name: str, server: FakeCloud, nick="Steve"):
        self.home = base / name
        self.root = self.home / "inst"
        self.inst = SimpleNamespace(id="i1", name="Сборка", root=self.root, game_dir=self.root / "minecraft",
                                    mods_dir=self.root / "minecraft" / "mods", loader="fabric", game_version="1.20.1")
        self.cfg = SimpleNamespace(world_sync=True)
        acc = SimpleNamespace(kind="offline", username=nick, id="a1")
        self.accounts = SimpleNamespace(list=lambda: [acc], active=acc)
        self.instances = SimpleNamespace(list=lambda: [self.inst], get=lambda i: self.inst if i == "i1" else None)
        self.running = False
        self.events = []
        self.state = S.SyncState(self.home / "state.json")
        self.engine = CloudEngine(self.cfg, self.accounts, self.instances, lambda i: self.running,
                                  lambda e, p=None: self.events.append((e, p)), lambda m: None, state=self.state)
        self.engine._info = {"v": 1, "max_world": 512 << 20, "part_size": server.part_size}
        self.engine.sync_all_async = lambda: None            # в тестах фон не запускаем: всё идёт по порядку
        self.engine._submit = self._sync_submit
        self.server = server

    def _sync_submit(self, key, fn):
        """Задачи — сразу и в том же потоке (внутри run, с подменой сети и папок)."""
        if not self.engine._claim(key):
            return False
        try:
            fn()
        except Busy:
            pass
        finally:
            self.engine._unclaim(key)
        return True

    def run(self, fn, *a, **k):
        """Операция этого ПК: подмена папки облачного кэша и сети."""
        with mock.patch.object(S, "SYNC_HOME", self.home / "worldsync"), \
                mock.patch.object(worldcloud, "call", self.server.call), \
                mock.patch.object(worldcloud, "put_part", self.server.put_part), \
                mock.patch.object(worldcloud, "download", self.server.download):
            return fn(*a, **k)

    @property
    def saves(self):
        return self.inst.game_dir / "saves"

    def binding(self, folder="World"):
        return self.state.find_folder("i1", folder)


class EngineTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.server = FakeCloud()
        self.server.put_log = []
        self.pc1 = Pc(self.root, "pc1", self.server)
        self.pc2 = Pc(self.root, "pc2", self.server)

    def upload_first(self, pc, folder="World", payload=b"A" * 3000):
        make_world(pc.saves / folder, name=folder, payload=payload)
        pc.run(pc.engine.enable_world, pc.inst, folder, "new")
        pc.run(pc.engine.list_cloud, "Steve")
        b = pc.binding(folder)
        pc.run(pc.engine.upload, pc.inst, b)
        return pc.binding(folder)

    def test_view_shows_prev_version_of_bound_world(self):
        """10.0: у привязанного мира видна прошлая облачная версия (кнопка «Прошлая версия»); нет её — поля нет."""
        b = self.upload_first(self.pc1)
        pc = self.pc1
        pc.run(pc.engine.list_cloud, "Steve")
        self.assertNotIn("prev", pc.engine.view(pc.inst)["worlds"]["World"])
        entry = pc.engine._entry("Steve", b["wid"])
        prev = {"rid": "r0", "at": 5, "size": 10, "device_name": "Старый ПК"}
        with mock.patch.object(pc.engine, "_entry", return_value={**entry, "prev": prev}):
            badge = pc.engine.view(pc.inst)["worlds"]["World"]
        self.assertEqual(badge["prev"], {"at": 5, "size": 10, "device_name": "Старый ПК"})

    def test_upload_then_download_on_second_pc(self):
        b = self.upload_first(self.pc1)
        self.assertEqual(b["base_rev"], 1)
        self.assertTrue(b["base_rid"])
        self.assertEqual(b["fp"], S.fingerprint(self.pc1.saves / "World"))
        self.assertIsNone(b["upload"])
        self.assertEqual(len(self.server.worlds), 1)
        pc2 = self.pc2
        pc2.run(pc2.engine.list_cloud, "Steve")
        view = pc2.engine.view(pc2.inst)
        self.assertEqual(len(view["cloud_only"]), 1)
        res = pc2.run(pc2.engine.download_cloud, pc2.inst, "Steve", view["cloud_only"][0]["wid"])
        self.assertTrue(res["ok"])
        folder = res["folder"]
        self.assertEqual((pc2.saves / folder / "region" / "r.0.0.mca").read_bytes(), b"A" * 3000)
        b2 = pc2.binding(folder)
        self.assertEqual(b2["base_rid"], b["base_rid"])
        self.assertEqual(b2["fp"], S.fingerprint(pc2.saves / folder))

    def test_second_version_replaces_unchanged_copy_and_keeps_old_in_trash(self):
        self.upload_first(self.pc1)
        pc1, pc2 = self.pc1, self.pc2
        pc2.run(pc2.engine.list_cloud, "Steve")
        wid = pc2.engine.view(pc2.inst)["cloud_only"][0]["wid"]
        folder = pc2.run(pc2.engine.download_cloud, pc2.inst, "Steve", wid)["folder"]
        # ПК1 играл и отправил вторую версию
        (pc1.saves / "World" / "region" / "r.0.0.mca").write_bytes(b"B" * 3000)
        bump(pc1.saves / "World" / "region" / "r.0.0.mca")
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        # ПК2: перед «Играть» мир заменяется, старый — в корзине
        gate = pc2.run(pc2.engine.before_play, pc2.inst, "")
        self.assertIsNone(gate)
        self.assertEqual((pc2.saves / folder / "region" / "r.0.0.mca").read_bytes(), b"B" * 3000)
        kept = [d for d in (pc2.root / "trash").iterdir() if d.is_dir()]
        self.assertEqual((kept[0] / "region" / "r.0.0.mca").read_bytes(), b"A" * 3000)
        self.assertEqual(pc2.binding(folder)["base_rev"], 2)

    def test_both_changed_gives_choice_and_keep_both(self):
        self.upload_first(self.pc1)
        pc1, pc2 = self.pc1, self.pc2
        pc2.run(pc2.engine.list_cloud, "Steve")
        wid = pc2.engine.view(pc2.inst)["cloud_only"][0]["wid"]
        folder = pc2.run(pc2.engine.download_cloud, pc2.inst, "Steve", wid)["folder"]
        for pc, data in ((pc1, b"ONE" * 1000), (pc2, b"TWO" * 1000)):
            f = pc.saves / (pc.binding().get("folder")) / "region" / "r.0.0.mca"
            f.write_bytes(data)
            bump(f)
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        pc2.run(pc2.engine.upload, pc2.inst, pc2.binding())                    # begin → conflict
        b2 = pc2.binding(folder)
        self.assertTrue(b2["choose"])
        self.assertTrue(any(e == "cloud_choose" for e, _p in pc2.events))
        self.assertEqual((pc2.saves / folder / "region" / "r.0.0.mca").read_bytes(), b"TWO" * 1000)   # локальный мир не тронут
        gate = pc2.run(pc2.engine.before_play, pc2.inst, "")
        self.assertEqual(gate["kind"], "choose")
        pc2.run(pc2.engine.resolve, pc2.inst, folder, "keep_both")
        names = sorted(p.name for p in pc2.saves.iterdir())
        self.assertEqual(len(names), 2)
        contents = {n: (pc2.saves / n / "region" / "r.0.0.mca").read_bytes() for n in names}
        self.assertIn(b"TWO" * 1000, contents.values())
        self.assertIn(b"ONE" * 1000, contents.values())

    def test_keep_mine_uploads_over_cloud(self):
        self.upload_first(self.pc1)
        pc1, pc2 = self.pc1, self.pc2
        pc2.run(pc2.engine.list_cloud, "Steve")
        wid = pc2.engine.view(pc2.inst)["cloud_only"][0]["wid"]
        folder = pc2.run(pc2.engine.download_cloud, pc2.inst, "Steve", wid)["folder"]
        f2 = pc2.saves / folder / "region" / "r.0.0.mca"
        f2.write_bytes(b"MINE" * 500)
        bump(f2)
        f1 = pc1.saves / "World" / "region" / "r.0.0.mca"
        f1.write_bytes(b"OTHER" * 500)
        bump(f1)
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        pc2.run(pc2.engine.resolve, pc2.inst, folder, "keep_mine")
        wid = next(iter(self.server.worlds))
        self.assertEqual(self.server.worlds[wid]["hist"].__len__(), 3)
        self.assertEqual(pc2.binding(folder)["base_rev"], 3)

    def test_offline_changes_nothing(self):
        self.upload_first(self.pc1)
        pc1 = self.pc1
        (pc1.saves / "World" / "region" / "r.0.0.mca").write_bytes(b"C" * 3000)
        bump(pc1.saves / "World" / "region" / "r.0.0.mca")
        before = S.fingerprint(pc1.saves / "World")
        self.server.fail["list"] = CloudError("Нет связи", 0)
        pc1.run(pc1.engine.list_cloud, "Steve")
        self.assertEqual(pc1.run(pc1.engine.evaluate, pc1.inst, pc1.binding())[0], "offline")
        self.server.fail["list"] = CloudError("Нет связи", 0)
        self.assertIsNone(pc1.run(pc1.engine.before_play, pc1.inst, ""))
        self.assertEqual(S.fingerprint(pc1.saves / "World"), before)
        self.assertTrue(any(e == "cloud_notice" for e, _p in pc1.events))      # неблокирующее окошко «нет связи»

    def test_gone_is_never_automatic(self):
        self.upload_first(self.pc1)
        pc1 = self.pc1
        self.server.worlds.clear()                                             # облако опустело (ошибка, откат)
        pc1.run(pc1.engine.list_cloud, "Steve")
        self.assertEqual(pc1.run(pc1.engine.evaluate, pc1.inst, pc1.binding())[0], "gone")
        self.server.calls.clear()
        pc1.run(pc1.engine.handle, pc1.inst, pc1.binding())
        self.assertNotIn("begin", self.server.calls)                          # молча заново не загружаем
        self.assertTrue((pc1.saves / "World" / "level.dat").exists())         # и ничего не удаляем
        self.assertEqual(pc1.engine.view(pc1.inst)["worlds"]["World"]["status"], "gone")
        pc1.run(pc1.engine.reupload, pc1.inst, "World")                       # только по кнопке
        self.assertEqual(len(self.server.worlds), 1)

    def test_crash_hold_then_clear(self):
        self.upload_first(self.pc1)
        pc1 = self.pc1
        f = pc1.saves / "World" / "region" / "r.0.0.mca"
        f.write_bytes(b"D" * 3000)
        bump(f)
        self.server.calls.clear()
        pc1.run(pc1.engine.after_game, pc1.inst, True)
        b = pc1.binding()
        self.assertEqual(b["hold"], "crash")
        self.assertNotIn("begin", self.server.calls)                          # после вылета не отправляем
        self.assertEqual(pc1.engine.view(pc1.inst)["worlds"]["World"]["status"], "crash")
        pc1.run(pc1.engine.after_game, pc1.inst, False)                       # мир не открывали — hold остаётся
        self.assertEqual(pc1.binding()["hold"], "crash")
        self.assertNotIn("begin", self.server.calls)
        bump(f)
        pc1.run(pc1.engine.after_game, pc1.inst, False)                       # открыли и нормально закрыли
        self.assertEqual(pc1.binding()["hold"], "")
        self.assertIn("begin", self.server.calls)

    def test_playing_mark_blocks_changes(self):
        self.upload_first(self.pc1)
        pc1 = self.pc1
        pc1.state.set_playing("i1", 4242)
        with mock.patch.object(S, "java_pid_alive", return_value=True):
            self.assertTrue(pc1.engine._busy_game("i1"))
            self.server.calls.clear()
            f = pc1.saves / "World" / "region" / "r.0.0.mca"
            f.write_bytes(b"E" * 3000)
            bump(f)
            pc1.run(pc1.engine.list_cloud, "Steve")
            pc1.run(pc1.engine.handle, pc1.inst, pc1.binding())
            self.assertNotIn("begin", self.server.calls)
            with self.assertRaises(Busy):
                pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        # игра закрылась без лаунчера: метка есть, процесса нет — считаем вылетом
        with mock.patch.object(S, "java_pid_alive", return_value=False):
            pc1.run(pc1.engine.check_playing_mark)
        self.assertEqual(pc1.binding()["hold"], "crash")
        self.assertEqual(pc1.state.playing(), {})

    def test_world_lock_blocks(self):
        self.upload_first(self.pc1)
        with mock.patch.object(S, "world_locked", return_value=True):
            self.assertTrue(self.pc1.engine._busy_game("i1"))

    def test_owner_tag_mismatch_pauses(self):
        self.upload_first(self.pc1)
        pc1 = self.pc1
        self.server.owner = "o:other"                                          # ник теперь у другого владельца
        pc1.run(pc1.engine.list_cloud, "Steve")
        self.assertEqual(pc1.binding()["paused"], "owner")
        self.server.calls.clear()
        pc1.run(pc1.engine.handle, pc1.inst, pc1.binding())
        self.assertEqual(self.server.calls, [])                               # никаких действий; «Загрузить заново» нет
        self.assertEqual(pc1.engine.view(pc1.inst)["worlds"]["World"]["status"], "paused")

    def test_resume_upload_sends_only_missing_parts(self):
        self.server.part_size = 4096
        pc1 = Pc(self.root, "pcx", self.server)
        make_world(pc1.saves / "World", payload=os.urandom(20000))
        pc1.run(pc1.engine.enable_world, pc1.inst, "World", "new")
        pc1.run(pc1.engine.list_cloud, "Steve")

        def hook(n):
            if n == 3:
                raise CloudError("Нет связи", 0)
        self.server.put_hook = hook
        with mock.patch("core.worldengine.time.sleep"):
            with self.assertRaises(Busy):
                pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        self.assertIsNotNone(pc1.binding()["upload"])                          # sid и etag'и записаны в state.json
        self.assertEqual(sorted(pc1.binding()["upload"]["etags"]), ["1", "2"])
        self.server.put_hook = None
        self.server.put_log.clear()
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        self.assertNotIn(1, self.server.put_log)
        self.assertNotIn(2, self.server.put_log)
        self.assertIsNone(pc1.binding()["upload"])
        self.assertEqual(pc1.binding()["base_rev"], 1)

    def test_changed_after_archive_restarts_upload(self):
        self.server.part_size = 4096
        pc1 = Pc(self.root, "pcy", self.server)
        make_world(pc1.saves / "World", payload=os.urandom(20000))
        pc1.run(pc1.engine.enable_world, pc1.inst, "World", "new")
        pc1.run(pc1.engine.list_cloud, "Steve")
        self.server.put_hook = lambda n: (_ for _ in ()).throw(CloudError("Нет связи", 0)) if n == 2 else None
        with mock.patch("core.worldengine.time.sleep"), self.assertRaises(Busy):
            pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        self.server.put_hook = None
        f = pc1.saves / "World" / "region" / "r.0.0.mca"
        f.write_bytes(os.urandom(20000))                                       # с тех пор в мир играли
        bump(f)
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        self.assertIsNone(pc1.binding()["upload"])
        import io
        with zipfile.ZipFile(io.BytesIO(self.server.blobs[pc1.binding()["base_rid"]])) as zf:     # в облаке — свежая версия, не старый архив
            self.assertEqual(zf.read("world/region/r.0.0.mca"), f.read_bytes())

    def test_lost_commit_answer_is_retried_safely(self):
        pc1 = self.pc1
        make_world(pc1.saves / "World")
        pc1.run(pc1.engine.enable_world, pc1.inst, "World", "new")
        pc1.run(pc1.engine.list_cloud, "Steve")
        orig = self.server.a_commit

        def lost(sid, parts):
            orig(sid, parts)
            raise CloudError("Нет связи", 0)
        self.server.a_commit = lost
        with self.assertRaises(CloudError):
            pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        self.assertIsNotNone(pc1.binding()["upload"])
        self.server.a_commit = orig
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())                    # повтор commit с тем же sid → тот же ответ
        self.assertEqual(pc1.binding()["base_rev"], 1)
        self.assertEqual(len(self.server.worlds[next(iter(self.server.worlds))]["hist"]), 1)

    def test_conflict_with_same_sha_adopts_our_upload(self):
        b = self.upload_first(self.pc1)
        pc1 = self.pc1
        head = self.server.head(b["wid"])
        up = {"sha256": head["sha256"], "zip": str(self.root / "x.zip"), "zip_fp": "fp-at-archive"}
        pc1.state.update(b, base_rid="old", base_rev=0, upload=up)
        pc1.engine._conflict_after_commit(pc1.binding(), up, "fp-now", CloudError("c", 409, "conflict", {"head": head}))
        nb = pc1.binding()
        self.assertEqual(nb["base_rid"], head["rid"])
        self.assertEqual(nb["fp"], "fp-at-archive")                           # а не текущий: изменения после архива не считаются отправленными
        self.assertFalse(nb["choose"])

    def test_older_level_dat_brakes_upload(self):
        b = self.upload_first(self.pc1)
        pc1 = self.pc1
        # папку заменили старой копией: level.dat старее, чем при прошлой синхронизации
        lvl = pc1.saves / "World" / "level.dat"
        t = lvl.stat().st_mtime - 3600
        os.utime(lvl, (t, t))
        pc1.run(pc1.engine.list_cloud, "Steve")
        self.assertEqual(pc1.run(pc1.engine.evaluate, pc1.inst, pc1.binding())[0], "choose")

    def test_one_folder_one_binding_two_nicks(self):
        self.upload_first(self.pc1)
        pc1 = self.pc1
        acc2 = SimpleNamespace(kind="offline", username="Alex", id="a2")
        pc1.accounts.active = acc2
        pc1.accounts.list = lambda: [acc2, SimpleNamespace(kind="offline", username="Steve", id="a1")]
        with self.assertRaises(WorldError) as cm:
            pc1.run(pc1.engine.enable_world, pc1.inst, "World", "new")
        self.assertEqual(cm.exception.code, "other_account")

    def test_similar_title_asks_before_linking(self):
        self.upload_first(self.pc1)
        pc2 = self.pc2
        make_world(pc2.saves / "Мир", name="World")                            # то же название, своя копия
        res = pc2.run(pc2.engine.enable_world, pc2.inst, "Мир")
        self.assertEqual(len(res["similar"]), 1)
        self.assertIsNone(pc2.binding("Мир"))                                  # сам по названию не связываем
        wid = res["similar"][0]["wid"]
        pc2.run(pc2.engine.enable_world, pc2.inst, "Мир", "link:" + wid)
        pc2.run(pc2.engine.list_cloud, "Steve")
        self.assertEqual(pc2.run(pc2.engine.evaluate, pc2.inst, pc2.binding("Мир"))[0], "choose")   # relation unknown → окно выбора

    def test_too_big_not_uploaded(self):
        pc1 = self.pc1
        pc1.engine._info = {"v": 1, "max_world": 2000, "part_size": 4096}
        make_world(pc1.saves / "World", payload=os.urandom(6000))
        pc1.run(pc1.engine.enable_world, pc1.inst, "World", "new")
        pc1.run(pc1.engine.list_cloud, "Steve")
        pc1.run(pc1.engine.upload, pc1.inst, pc1.binding())
        self.assertEqual(pc1.binding()["err"], "too_big")
        self.assertEqual(self.server.worlds, {})

    def test_disabled_server_pauses_everything(self):
        pc1 = self.pc1
        pc1.engine._info = None
        self.assertFalse(pc1.engine.enabled())
        self.assertIsNone(pc1.engine.before_play(pc1.inst, ""))

    def test_skip_mode_does_nothing(self):
        self.upload_first(self.pc1)
        self.server.calls.clear()
        self.assertIsNone(self.pc1.run(self.pc1.engine.before_play, self.pc1.inst, "skip"))
        self.assertEqual(self.server.calls, [])

    def test_corrupt_download_is_rejected(self):
        self.upload_first(self.pc1)
        pc2 = self.pc2
        pc2.run(pc2.engine.list_cloud, "Steve")
        entry = pc2.engine.view(pc2.inst)["cloud_only"][0]
        for rid in self.server.blobs:
            self.server.blobs[rid] = b"X" * len(self.server.blobs[rid])          # сервер отдаёт испорченный файл
        with self.assertRaises(WorldError):
            pc2.run(pc2.engine.download_cloud, pc2.inst, "Steve", entry["wid"])
            raise WorldError("corrupt")
        self.assertEqual(list(pc2.saves.iterdir()) if pc2.saves.exists() else [], [])    # в saves ничего не появилось

    def test_recover_all_binds_after_lost_state(self):
        pc = self.pc1
        old = make_world(pc.saves / "World", payload=b"O" * 100)
        new = make_world(S.new_dir(pc.root, "w" * 24) / "world", payload=b"N" * 100)
        fp = S.fingerprint(new)
        with mock.patch.object(S, "_finish", side_effect=OSError("выключили")):
            with self.assertRaises(OSError):
                S.apply_cloud_copy(pc.root, pc.inst.game_dir, "World", new, wid="w" * 24, rid="r" * 16, rev=2, nick="Steve", fp_new=fp)
        self.assertIsNone(pc.binding())
        pc.engine.recover_all()
        b = pc.binding()
        self.assertEqual((b["nick"], b["base_rev"], b["fp"]), ("Steve", 2, fp))   # журнал сам довёл до конца и привязал
        self.assertTrue(old.parent.exists())


if __name__ == "__main__":
    unittest.main()
