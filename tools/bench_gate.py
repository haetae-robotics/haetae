"""Real signed Rust enforcer with random, ephemeral, trusted-host bench fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ros" / "haetae_gate"))
from bridge import Bridge, BridgeFailure, lease_renewable, require_fresh_actuation
from signing import Signer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def now_ms():
    return time.time_ns() // 1_000_000


class BenchGate:
    def __init__(self, binary):
        self.temp = tempfile.TemporaryDirectory(prefix="haetae-led-")
        root = Path(self.temp.name)
        root.chmod(0o700)
        self.bridge = None
        try:
            self.initialize(root, binary)
        except BaseException:
            self.close()
            raise

    def initialize(self, root, binary):
        policy = {
            "allowed_sources": ["vla"],
            "freshness": {"world_max_age_ms": 200, "proposal_max_age_ms": 1000, "future_tolerance_ms": 20},
            "envelope": {"max_speed": 1, "workspace": {"min": {"x": 0, "y": 0}, "max": {"x": 10, "y": 10}}},
            "base": {"footprint_radius": .25, "max_decel": 1, "max_angular": 1, "latency_ms": 50, "max_ttl_ms": 200},
            "rules": [{"id": "person", "when": {"human_within": {"distance": .3}}, "then": "bul"}]}
        raw = json.dumps(policy, separators=(",", ":")).encode()
        (root / "policy.json").write_bytes(raw)
        public = {}
        keys = {}
        for role in ("world", "fault", "vla", "log"):
            seed = os.urandom(32)
            path = root / (role + ".key")
            path.write_text(seed.hex())
            path.chmod(0o600)
            keys[role] = str(path)
            public[role] = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw).hex()
        anchor = Ed25519PrivateKey.generate()
        anchor_public = anchor.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        body = json.dumps({"v": 1, "audience": "uno-led-bench", "epoch": 1,
                           "policy_sha256": hashlib.sha256(raw).hexdigest(),
                           "keys": {role: public[role] for role in ("world", "fault", "vla")}},
                          separators=(",", ":"))
        (root / "trust.json").write_text(json.dumps({"body": body, "signature": anchor.sign(
            b"haetae-trust-bundle-v1\0" + body.encode()).hex()}))
        subprocess.run([str(binary), "state", "set", "--state", str(root / "state.json"),
                        "--mode", "normal", "--by", "led-bench", "--reason", "explicit bench start"],
                       check=True, stdout=subprocess.DEVNULL)
        self.signer = Signer(root / "trust.json", root / "state.json",
                             {role: keys[role] for role in ("world", "fault", "vla")})
        self.bridge = Bridge([str(binary), "enforce", "--stdio", "--policy", str(root / "policy.json"),
                              "--state", str(root / "state.json"), "--trust", str(root / "trust.json"),
                              "--root-pubkey", anchor_public, "--sillok", str(root / "sillok.jsonl"),
                              "--key", keys["log"]], timeout_ms=500)
        self.proposal = 0
        self.last_motion = None
        self.world(False)  # Bounded OFF-only process/trust/state bootstrap.
        self.world(False)  # Fresh receive timestamp after the startup wait.
        step = self.motion(stop=True)  # Signed zero re-arms the Rust source.
        if (step["cmd"] != {"linear": 0.0, "angular": 0.0}
                or step["status"]["mode"] != "normal" or step["status"]["armed"] != ["vla"]):
            raise BridgeFailure("OFF-only bootstrap did not arm the Rust source")
        # Zero responses precede their durable commit. An OFF-only tick is a
        # barrier for that bootstrap checkpoint before starting positive cycles.
        barrier = self.bridge.request({"k": "tick", "t": now_ms()})
        if barrier["cmd"] != {"linear": 0.0, "angular": 0.0} or barrier["status"]["armed"] != ["vla"]:
            raise BridgeFailure("OFF-only checkpoint barrier lost rearm")
        self.bridge.timeout = .050  # Every operational request retains the 50ms bound.

    def signed(self, envelope):
        return self.bridge.request({"t": now_ms(), "k": "signed", "data": json.dumps(envelope)})

    def world(self, person):
        humans = [{"id": "bench-person", "class": "child", "pos": {"x": 5.1, "y": 5}}] if person else []
        return self.signed(self.signer.sign("world", {"stamp_ms": now_ms(),
                           "robot": {"pose": {"x": 5, "y": 5}, "yaw": 0, "twist": {"linear": 0.0, "angular": 0.0}}, "humans": humans, "confidence": 1}))

    def motion(self, stop=False):
        self.proposal += 1
        action = {"type": "stop"} if stop else {"type": "velocity", "linear": .25, "angular": 0, "ttl_ms": 200}
        envelope = self.signer.sign("vla", {"id": self.proposal, "source": "vla", "timestamp_ms": now_ms(), "action": action})
        if not stop:
            self.last_motion = envelope
        return self.signed(envelope)

    def cycle(self, fault=None):
        started = time.monotonic()
        self.approval = None
        if fault != "world-loss":
            step = self.world(fault == "person")
            if step.get("stop") not in (None, "no_command", "startup") or step.get("outcome", {}).get("rejected"):
                return False
        if fault == "replay":
            step = self.signed(self.last_motion)
        elif fault == "invalid-signature":
            envelope = self.signer.sign("vla", {"id": 999, "source": "vla", "timestamp_ms": now_ms(),
                                                "action": {"type": "velocity", "linear": .25, "angular": 0, "ttl_ms": 200}})
            envelope["signature"] = "00" * 64
            step = self.signed(envelope)
        else:
            step = self.motion()
        elapsed = (time.monotonic() - started) * 1000
        require_fresh_actuation(step, elapsed, now_ms(), 200, 50)
        positive = step["cmd"]["linear"] != 0 or step["cmd"]["angular"] != 0
        if positive and lease_renewable(step, elapsed, 200, 50):
            self.approval = (step, started)
            return True
        return False

    def remaining(self):
        if self.approval is None:
            raise BridgeFailure("no positive gate approval")
        step, started = self.approval
        elapsed = (time.monotonic() - started) * 1000
        require_fresh_actuation(step, elapsed, now_ms(), 200, 50)
        return (50 - elapsed) / 1000

    def close(self):
        if self.bridge:
            self.bridge.close()
        self.temp.cleanup()
