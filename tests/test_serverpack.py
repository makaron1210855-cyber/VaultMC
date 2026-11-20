"""Сервер из сборки: какие моды идут на сервер (core/serverside.py), файлы папки сервера (core/serverpack.py)."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import serverpack as SP, serverside as SS  # noqa: E402
from core.utils import offline_uuid  # noqa: E402


def fabric_jar(path: Path, mod_id: str, env: str = "*", depends: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps({"schemaVersion": 1, "id": mod_id, "name": mod_id.title(),
                                                  "version": "1.0", "environment": env, "depends": depends or {}}))
    return path


def forge_jar(path: Path, mod_id: str, client_only: bool = False) -> Path:
    toml = ("clientSideOnly=true\n" if client_only else "") + f'modLoader="javafml"\n[[mods]]\nmodId="{mod_id}"\nversion="1"\n'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/mods.toml", toml)
    return path


class Decide(unittest.TestCase):
    def side(self, ids, jar="", mr="", file="x.jar"):
        m = SS.ModSide(file, "x", "both", "both", ids)
        SS.decide(m, jar, mr)
        return m.side, m.reason

    def test_order_of_sources(self):
        self.assertEqual(self.side(["foo"], jar="client"), ("client", "jar"))
        self.assertEqual(self.side(["foo"], jar="server"), ("server", "server"))
        self.assertEqual(self.side(["foo"], mr="client"), ("client", "modrinth"))
        self.assertEqual(self.side(["sodium"]), ("client", "list"))
        self.assertEqual(self.side(["lithium"]), ("both", "both"))
        self.assertEqual(self.side(["e4mc"], jar="*"), ("client", "tunnel"))
        self.assertEqual(self.side([], file="OptiFine_1.20.1_HD_U_I6.jar"), ("client", "list"))

    def test_list_loses_to_modrinth_both_and_doubtful(self):
        self.assertEqual(self.side(["sodium"], mr="both"), ("both", "both"))
        self.assertEqual(self.side(["xaerominimap"]), ("both", "both"))         # бывает и серверным
        self.assertEqual(self.side(["xaerominimap"], mr="client"), ("client", "modrinth"))

    def test_dependency_comes_back(self):
        lib = SS.ModSide("lib.jar", "Lib", "client", "modrinth", ["clientlib"])
        user = SS.ModSide("a.jar", "A", "both", "both", ["a"], ["clientlib"])
        jar_client = SS.ModSide("c.jar", "C", "client", "jar", ["cc"])
        user2 = SS.ModSide("b.jar", "B", "both", "both", ["b"], ["cc"])
        SS.keep_dependencies([lib, user, jar_client, user2])
        self.assertEqual((lib.side, lib.reason), ("both", "dep"))
        self.assertEqual(jar_client.side, "client")       # автор сам отметил клиентским — загрузчик его не загрузит


class Classify(unittest.TestCase):
    def test_reads_jars_without_network(self):
        with tempfile.TemporaryDirectory() as t:
            mods = Path(t)
            fabric_jar(mods / "sodium.jar", "sodium", "client")
            fabric_jar(mods / "lithium.jar", "lithium", "*")
            forge_jar(mods / "ui.jar", "fancyui", client_only=True)
            forge_jar(mods / "ores.jar", "ores")
            fabric_jar(mods / "off.jar.disabled", "off")
            got = {m.file: m.on_server for m in SS.classify(mods, online=False)}
        self.assertEqual(got, {"lithium.jar": True, "ores.jar": True, "sodium.jar": False, "ui.jar": False})

    def test_modrinth_answer(self):
        versions = {"aa": {"project_id": "P1"}, "bb": {"project_id": "P2"}}
        projects = [{"id": "P1", "server_side": "unsupported", "client_side": "required"},
                    {"id": "P2", "server_side": "required", "client_side": "optional"}]
        with mock.patch("core.net.post_json", return_value=versions), mock.patch("core.net.get_json", return_value=projects):
            self.assertEqual(SS.modrinth_sides(["aa", "bb"]), {"aa": "client", "bb": "both"})
        with mock.patch("core.net.post_json", side_effect=OSError("нет сети")):
            self.assertEqual(SS.modrinth_sides(["aa"]), {})


class Options(unittest.TestCase):
    def test_parse_and_validate(self):
        o = SP.ServerOptions.parse({"mods": ["a.jar", "../evil.jar"], "port": "25570", "ram_gb": 99,
                                    "whitelist": ["Steve", "Steve", "Alex"], "op": "Steve", "eula": 1})
        self.assertEqual((o.mods, o.port, o.ram_gb, o.whitelist, o.eula), (["a.jar"], 25570, 64, ["Steve", "Alex"], True))
        for bad in ({"port": 80}, {"whitelist": ["плохой ник"]}, {"world": "../saves"}, {"port": "x"}):
            with self.assertRaises(SP.ServerError):
                SP.ServerOptions.parse(bad)


class Files(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_properties_keep_owner_edits(self):
        p = self.d / "server.properties"
        p.write_text("#x\nmotd=Мой сервер\nserver-port=1\npvp=false\n", encoding="utf-8")
        SP.set_properties(p, {"server-port": 25566}, {"motd": "VaultMC", "online-mode": "false"})
        text = p.read_text(encoding="utf-8")
        self.assertIn("motd=Мой сервер", text)
        self.assertIn("server-port=25566", text)
        self.assertIn("pvp=false", text)
        self.assertIn("online-mode=false", text)
        self.assertEqual(text.count("server-port"), 1)

    def test_players(self):
        SP.write_players(self.d, SP.ServerOptions(whitelist=["Alex"], op="Steve"))
        wl = json.loads((self.d / "whitelist.json").read_text())
        self.assertEqual([w["name"] for w in wl], ["Alex", "Steve"])
        self.assertEqual(wl[0]["uuid"], offline_uuid("Alex"))
        SP.write_players(self.d, SP.ServerOptions(op="steve"))     # тот же оператор другим регистром — не дублируем
        self.assertEqual(len(json.loads((self.d / "ops.json").read_text())), 1)

    def test_sync_mods_removes_only_own(self):
        src, dst = self.d / "src", self.d / "dst"
        src.mkdir()
        dst.mkdir()
        for n in ("a.jar", "b.jar"):
            (src / n).write_bytes(n.encode())
        (dst / "old.jar").write_bytes(b"1")           # лаунчер положил в прошлый раз
        (dst / "mine.jar").write_bytes(b"2")          # владелец сервера положил сам
        got = SP.sync_mods(src, dst, ["a.jar", "gone.jar"], ["old.jar"])
        self.assertEqual(got, ["a.jar"])
        self.assertEqual(sorted(p.name for p in dst.iterdir()), ["a.jar", "mine.jar"])

    def test_forge_launch_args(self):
        lib = self.d / "libraries/net/minecraftforge/forge/1.20.1-47.3.0"
        lib.mkdir(parents=True)
        (lib / "win_args.txt").write_text("x")
        self.assertEqual(SP.forge_launch_args(self.d, "47.3.0"),
                         ["@libraries/net/minecraftforge/forge/1.20.1-47.3.0/win_args.txt", "nogui"])
        legacy = self.d / "old"
        legacy.mkdir()
        (legacy / "forge-1.12.2-14.23.5.2860-installer.jar").write_bytes(b"")
        (legacy / "forge-1.12.2-14.23.5.2860.jar").write_bytes(b"")
        self.assertEqual(SP.forge_launch_args(legacy, "14.23.5.2860"), ["-jar", "forge-1.12.2-14.23.5.2860.jar", "nogui"])
        with self.assertRaises(SP.ServerError):
            SP.forge_launch_args(self.d / "nothing", "1")

    def test_scripts_and_zip(self):
        SP.write_scripts(self.d, ["@libraries/x/win_args.txt", "nogui"], 6, 17, "Тест")
        self.assertIn("unix_args.txt", (self.d / "start.sh").read_text())
        self.assertIn("-Xmx6G", (self.d / "start.bat").read_text(encoding="utf-8"))
        (self.d / "world").mkdir()
        (self.d / "world" / "level.dat").write_bytes(b"w")
        (self.d / "world" / "session.lock").write_bytes(b"")
        (self.d / "logs").mkdir()
        (self.d / "logs" / "latest.log").write_text("x")
        out = Path(self.t.name + "-a.zip")
        try:
            SP.export_zip(self.d, out, with_world=True)
            with zipfile.ZipFile(out) as z:
                names = set(z.namelist())
                mode = z.getinfo("start.sh").external_attr >> 16
            self.assertIn("world/level.dat", names)
            self.assertNotIn("world/session.lock", names)
            self.assertNotIn("logs/latest.log", names)
            self.assertEqual(mode & 0o777, 0o755)
            SP.export_zip(self.d, out, with_world=False)
            with zipfile.ZipFile(out) as z:
                self.assertFalse(any(n.startswith("world/") for n in z.namelist()))
        finally:
            out.unlink(missing_ok=True)

    def test_world_copy_skips_lock_and_dh(self):
        inst = SimpleNamespace(game_dir=self.d / "game")
        w = inst.game_dir / "saves" / "Мир"
        (w / "data").mkdir(parents=True)
        (w / "level.dat").write_bytes(b"1")
        (w / "session.lock").write_bytes(b"")
        (w / "data" / "DistantHorizons.sqlite").write_bytes(b"big")
        srv = self.d / "srv"
        srv.mkdir()
        self.assertEqual(SP._place_world(inst, srv, SP.ServerOptions(world="Мир")), "copied")
        self.assertTrue((srv / "world" / "level.dat").exists())
        self.assertFalse((srv / "world" / "session.lock").exists())
        self.assertFalse((srv / "world" / "data" / "DistantHorizons.sqlite").exists())
        self.assertEqual(SP._place_world(inst, srv, SP.ServerOptions(world="Мир")), "kept")
        self.assertEqual(SP._place_world(inst, srv, SP.ServerOptions(world="Мир", replace_world=True)), "copied")
        self.assertTrue(any(p.name.startswith("world-old-") for p in srv.iterdir()))


class Loader(unittest.TestCase):
    def test_loader_of(self):
        inst = lambda loader, vid, gv="1.20.1": SimpleNamespace(loader=loader, version_id=vid, game_version=gv)  # noqa: E731
        self.assertEqual(SP.loader_of(inst("fabric", "fabric-loader-0.16.5-1.20.1")), ("fabric", "1.20.1", "0.16.5"))
        self.assertEqual(SP.loader_of(inst("forge", "1.20.1-forge-47.3.0")), ("forge", "1.20.1", "47.3.0"))
        self.assertEqual(SP.loader_of(inst("neoforge", "neoforge-21.1.77", "1.21.1")), ("neoforge", "1.21.1", "21.1.77"))
        self.assertEqual(SP.loader_of(inst("quilt", "quilt-loader-0.26.0-1.20.1")), ("quilt", "1.20.1", "0.26.0"))
        self.assertEqual(SP.loader_of(inst("vanilla", "1.21.1", "1.21.1")), ("vanilla", "1.21.1", ""))
        with self.assertRaises(SP.ServerError):
            SP.loader_of(inst("forge", "something-odd"))


if __name__ == "__main__":
    unittest.main()
