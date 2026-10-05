import json
import os
from pathlib import Path
import tempfile
import unittest

from permit_keys import deployment, private_bytes, provision
from permit_protocol import decode_json, frame, payload, bind_payload


class PermitContractTest(unittest.TestCase):
    def setUp(self):
        self.value = dict(install="01"*16, epoch=1, generation=1, sequence=0, challenge=1, issued=0,
                          nonce="02"*16, op="ARM")

    def test_fixed_binary_encoding_and_action_binding(self):
        encoded = payload(self.value)
        self.assertEqual(len(encoded), 76)
        self.assertEqual(encoded[:16], b"HAETAE-LED-H2\0\0\0")
        self.assertNotEqual(encoded, payload(dict(self.value, op="RUN")))
        self.assertNotEqual(encoded, payload(dict(self.value, epoch=2)))
        self.assertNotEqual(encoded, payload(dict(self.value, nonce="03"*16)))

    def test_strict_request_contract(self):
        for name, bad in [("epoch", True), ("sequence", -1), ("challenge", 0),
                          ("issued", 2**32), ("op", "MOTOR"), ("nonce", "GG"*16)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload(dict(self.value, **{name: bad}))
        with self.assertRaises(ValueError):
            payload(dict(self.value, decision=True))
        with self.assertRaises(ValueError):
            decode_json(b'{"epoch":1,"epoch":2}')
        with self.assertRaises(ValueError):
            decode_json(b" "*513)
        with self.assertRaises(ValueError):
            frame(self.value, "00"*63)
        bind = dict(self.value, op="BIND")
        self.assertEqual(len(bind_payload(bind, "03"*32, "04"*32)), 140)
        with self.assertRaises(ValueError):
            bind_payload(self.value, "03"*32, "04"*32)
        with self.assertRaises(ValueError):
            frame(bind, "00"*64, 200, "03"*32)

    def test_explicit_private_provisioning_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device"
            config = provision(path)
            self.assertEqual(deployment(path / "deployment.json"), config)
            self.assertEqual(len(private_bytes(path / "authorizer.seed")), 32)
            with self.assertRaises(FileExistsError):
                provision(path)
            (path / "authorizer.seed").chmod(0o644)
            with self.assertRaises(ValueError):
                private_bytes(path / "authorizer.seed")
            (path / "authorizer.seed").chmod(0o600)
            os.symlink(path / "authorizer.seed", path / "linked.seed")
            with self.assertRaises(OSError):
                private_bytes(path / "linked.seed")

    def test_authorizer_ignores_relay_decisions_and_latches(self):
        from permit_authorizer import Authorizer
        class Gate:
            def cycle(self, fault):
                return False
        authorizer = Authorizer.__new__(Authorizer)
        authorizer.config = {"install": self.value["install"]}
        authorizer.gate = Gate()
        authorizer.scenario, authorizer.seconds = "person", 1
        authorizer.started = None
        authorizer.binding = 1, 1
        authorizer.sequence = 0
        authorizer.challenge = 0
        authorizer.locked = False
        authorizer.last_fault = None
        authorizer.armed_authorized = False
        self.assertFalse(authorizer.approve(self.value)["allowed"])
        self.assertTrue(authorizer.locked)
        self.assertFalse(authorizer.approve(dict(self.value, generation=2))["allowed"])


if __name__ == "__main__":
    unittest.main()
