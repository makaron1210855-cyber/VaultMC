import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import net  # noqa: E402


class IsValidTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.good = b"GOOD" * 10
        self.sha = hashlib.sha1(self.good).hexdigest()
        self.path = self.dir / "f.jar"

    def test_same_size_wrong_content_is_rejected(self):
        self.path.write_bytes(b"BADX" * 10)  # та же длина, другое содержимое
        self.assertFalse(net.is_valid(self.path, self.sha, len(self.good)))

    def test_right_content_and_size_is_accepted(self):
        self.path.write_bytes(self.good)
        self.assertTrue(net.is_valid(self.path, self.sha, len(self.good)))

    def test_size_mismatch_rejected_fast(self):
        self.path.write_bytes(self.good[:-1])
        self.assertFalse(net.is_valid(self.path, self.sha, len(self.good)))

    def test_sha1_only(self):
        self.path.write_bytes(b"BADX" * 10)
        self.assertFalse(net.is_valid(self.path, self.sha, None))
        self.path.write_bytes(self.good)
        self.assertTrue(net.is_valid(self.path, self.sha, None))

    def test_no_info_means_exists(self):
        self.path.write_bytes(b"anything")
        self.assertTrue(net.is_valid(self.path, None, None))

    def test_missing_file(self):
        self.assertFalse(net.is_valid(self.dir / "none.jar", self.sha, len(self.good)))


if __name__ == "__main__":
    unittest.main()
