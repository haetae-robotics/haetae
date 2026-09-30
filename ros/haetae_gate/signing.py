"""Role-bound Ed25519 input envelopes for the Haetae subprocess.

The legacy transport signs in the gateway. The isolated Gazebo transport uses
separate role signers under distinct OS users and SROS2 enclaves. Its gateway
has no input signing keys. Requires ``cryptography``.
"""

import json
import os
import struct
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROLE_TAG = {"world": 1, "fault": 2, "vla": 3, "planner": 4, "teleop": 5, "peer": 6}


def input_message(audience, epoch, counter, role, payload):
    audience_bytes = audience.encode("utf-8")
    payload_bytes = payload.encode("utf-8")
    return (b"haetae-input-v1\0" + struct.pack(">I", len(audience_bytes)) + audience_bytes
            + struct.pack(">QQB", epoch, counter, ROLE_TAG[role])
            + struct.pack(">I", len(payload_bytes)) + payload_bytes)


class Signer:
    def __init__(self, trust_path, state_path, key_paths):
        bundle = json.loads(Path(trust_path).read_text())
        body = json.loads(bundle["body"])
        if body["v"] != 1 or not body["audience"] or body["epoch"] <= 0:
            raise ValueError("invalid trust bundle")
        self.audience = body["audience"]
        self.epoch = body["epoch"]
        self.keys = {}
        for role, path in key_paths.items():
            if role not in ROLE_TAG:
                raise ValueError("unknown signing role: " + role)
            seed_path = Path(path)
            if os.name == "posix" and (seed_path.stat().st_mode & 0o077 or
                                       seed_path.stat().st_uid != os.geteuid()):
                raise ValueError("signing seed must be owned by this user and owner-only: " + role)
            seed = bytes.fromhex(seed_path.read_text().strip())
            if len(seed) != 32:
                raise ValueError("signing seed must be 32 bytes")
            key = Ed25519PrivateKey.from_private_bytes(seed)
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            if public != body["keys"].get(role):
                raise ValueError("signing key is not in trust bundle: " + role)
            self.keys[role] = key
        self.counters = {role: 0 for role in key_paths}
        if state_path and Path(state_path).exists():
            state = json.loads(Path(state_path).read_text())
            if state.get("auth_epoch", 0) > self.epoch:
                raise ValueError("trust epoch rolled back")
            if state.get("auth_epoch") == self.epoch:
                self.counters.update({role: int(value) for role, value in state.get("counters", {}).items()
                                      if role in self.keys})

    def sign(self, role, payload):
        if role not in self.keys:
            raise ValueError("unconfigured signing role: " + role)
        if isinstance(payload, dict):
            payload = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        counter = self.counters.get(role, 0) + 1
        msg = input_message(self.audience, self.epoch, counter, role, payload)
        signature = self.keys[role].sign(msg).hex()
        self.counters[role] = counter
        return {"v": 1, "audience": self.audience, "epoch": self.epoch,
                "counter": counter, "role": role, "payload": payload, "signature": signature}
