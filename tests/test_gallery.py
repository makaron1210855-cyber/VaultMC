import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.worlds import recent_screenshots


class RecentScreenshotsTest(unittest.TestCase):
    def test_merges_and_sorts_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            insts = []
            for n, name in enumerate(("a", "b")):
                game = Path(tmp) / name
                (game / "screenshots").mkdir(parents=True)
                f = game / "screenshots" / f"{name}.png"
                f.write_bytes(b"x")
                import os
                os.utime(f, (time.time() - (10 - n * 5), time.time() - (10 - n * 5)))
                (game / "screenshots" / "note.txt").write_text("не картинка")
                insts.append(SimpleNamespace(id=name, name=name.upper(), game_dir=game))
            insts.append(SimpleNamespace(id="c", name="C", game_dir=Path(tmp) / "none"))
            shots = recent_screenshots(insts)
            self.assertEqual([s["inst"] for s in shots], ["b", "a"])
            self.assertEqual(recent_screenshots(insts, 1)[0]["name"], "B")


if __name__ == "__main__":
    unittest.main()
