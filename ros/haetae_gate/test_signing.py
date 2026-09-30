import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from signing import Signer, input_message


class SigningTests(unittest.TestCase):
    def test_role_counter_and_exact_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            private = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
            public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            (directory / "seed").write_text((bytes([7]) * 32).hex())
            (directory / "seed").chmod(0o600)
            (directory / "trust").write_text(json.dumps({"body": json.dumps({
                "v": 1, "audience": "robot-7", "epoch": 42, "keys": {"vla": public}}),
                "signature": "unused-by-signer"}))
            signer = Signer(directory / "trust", None, {"vla": str(directory / "seed")})
            signed = signer.sign("vla", {"id": 1})
            private.public_key().verify(bytes.fromhex(signed["signature"]), input_message(
                signed["audience"], signed["epoch"], signed["counter"], signed["role"], signed["payload"]))
            self.assertEqual(signer.sign("vla", "{}")["counter"], 2)
            (directory / "state").write_text(json.dumps({"auth_epoch": 42, "counters": {"vla": 9}}))
            signer = Signer(directory / "trust", directory / "state", {"vla": str(directory / "seed")})
            self.assertEqual(signer.sign("vla", "{}")["counter"], 10)
            (directory / "seed").chmod(0o644)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                Signer(directory / "trust", directory / "state", {"vla": str(directory / "seed")})


if __name__ == "__main__":
    unittest.main()
