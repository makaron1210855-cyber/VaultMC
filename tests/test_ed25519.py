"""Подпись обновлений: Ed25519 на чистом Python сверяется с векторами RFC 8032 и с Node (встроенный crypto)."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ed25519  # noqa: E402

# RFC 8032, раздел 7.1, TEST 1 и TEST 2
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]

NODE_SCRIPT = r"""
const crypto = require('crypto');
const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
const msg = Buffer.from(process.argv[1], 'utf8');
const sig = crypto.sign(null, msg, privateKey);
const raw = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
const seed = privateKey.export({ format: 'der', type: 'pkcs8' }).subarray(-32);
const mine = Buffer.from(process.argv[2], 'hex'), minePub = Buffer.from(process.argv[3], 'hex');
const der = Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), minePub]);
const ok = crypto.verify(null, msg, crypto.createPublicKey({ key: der, format: 'der', type: 'spki' }), mine);
console.log(JSON.stringify({ pub: raw.toString('hex'), seed: seed.toString('hex'), sig: sig.toString('hex'), pyOk: ok }));
"""


class Ed25519Tests(unittest.TestCase):
    def test_rfc_vectors(self):
        for secret, public, msg, sig in VECTORS:
            sk, m = bytes.fromhex(secret), bytes.fromhex(msg)
            self.assertEqual(ed25519.public_key(sk).hex(), public)
            self.assertEqual(ed25519.sign(sk, m).hex(), sig)
            self.assertTrue(ed25519.verify(bytes.fromhex(public), m, bytes.fromhex(sig)))

    def test_tampering_rejected(self):
        secret, public, msg, sig = VECTORS[1]
        pub, s = bytes.fromhex(public), bytearray(bytes.fromhex(sig))
        self.assertFalse(ed25519.verify(pub, b"\x73", bytes(s)), "другое сообщение")
        s[5] ^= 1
        self.assertFalse(ed25519.verify(pub, bytes.fromhex(msg), bytes(s)), "испорченная подпись")
        self.assertFalse(ed25519.verify(bytes.fromhex(VECTORS[0][1]), bytes.fromhex(msg), bytes.fromhex(sig)), "чужой ключ")
        self.assertFalse(ed25519.verify(pub, bytes.fromhex(msg), b"\x00" * 10), "короткая подпись")

    @unittest.skipUnless(shutil.which("node"), "нужен Node")
    def test_cross_check_with_node(self):
        msg = "vaultmc-update проверка 6.4.17"
        sk = bytes(range(32))
        mine = ed25519.sign(sk, msg.encode())
        out = subprocess.run(["node", "-e", NODE_SCRIPT, msg, mine.hex(), ed25519.public_key(sk).hex()],
                             capture_output=True, text=True, check=True)
        data = json.loads(out.stdout)
        self.assertTrue(data["pyOk"], "Node принимает подпись, сделанную Python")
        self.assertTrue(ed25519.verify(bytes.fromhex(data["pub"]), msg.encode(), bytes.fromhex(data["sig"])),
                        "Python принимает подпись, сделанную Node")
        self.assertEqual(ed25519.public_key(bytes.fromhex(data["seed"])).hex(), data["pub"])


if __name__ == "__main__":
    unittest.main()
