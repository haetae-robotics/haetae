"""Real signed Rust enforcer with random, ephemeral, trusted-host bench fixtures."""
import hashlib
import json
import math
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


DIAGNOSTIC_MODES = frozenset({"normal", "caution", "hold", "safe_park", "estop"})
DIAGNOSTIC_STOPS = frozenset({"startup", "no_world", "stale_world", "no_command", "expired", "denied",
                              "revoked", "unarmed", "recorder_fault", "state_fault", "arm_settling"})
DIAGNOSTIC_OUTCOMES = frozenset({"decision", "world_updated", "mode_changed", "rejected"})


def diagnostic_enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def diagnostic_uint(value):
    return value if type(value) is int and 0 <= value <= 2 ** 64 - 1 else None


def diagnostic_elapsed(value):
    return round(value, 3) if type(value) in (int, float) and 0 <= value <= 60000 and math.isfinite(value) else None


def diagnostic_stop(value):
    if isinstance(value, dict) and len(value) == 1 and "mode" in value:
        mode = diagnostic_enum(value["mode"], DIAGNOSTIC_MODES)
        return "mode:" + mode if mode is not None else None
    return diagnostic_enum(value, DIAGNOSTIC_STOPS)


def diagnostic_response(step, phase, request_ms, elapsed_ms, started_ns=None, completed_ns=None):
    """Fixed-size projection only: never retain errors, rule IDs, proposals or signed inputs."""
    step = step if isinstance(step, dict) else {}
    status = step.get("status") if isinstance(step.get("status"), dict) else {}
    outcome = step.get("outcome") if isinstance(step.get("outcome"), dict) else {}
    kind = next(iter(outcome)) if len(outcome) == 1 else None
    kind = diagnostic_enum(kind, DIAGNOSTIC_OUTCOMES)
    decision = outcome.get("decision") if isinstance(outcome.get("decision"), dict) else {}
    world = outcome.get("world_updated") if isinstance(outcome.get("world_updated"), dict) else {}
    fired = decision.get("fired")
    armed = status.get("armed")
    return {"phase": diagnostic_enum(phase, {"world", "motion"}),
            "request_t_ms": diagnostic_uint(request_ms), "elapsed_ms": diagnostic_elapsed(elapsed_ms),
            "request_started_ns": diagnostic_uint(started_ns), "response_received_ns": diagnostic_uint(completed_ns),
            "stop": diagnostic_stop(step.get("stop")), "outcome": kind,
            "verdict": diagnostic_enum(decision.get("verdict"), {"yun", "jeol", "bul"}),
            "fired_count": min(len(fired), 65535) if isinstance(fired, list) else None,
            "decision_expires_ms": diagnostic_uint(decision.get("expires_ms")),
            "world_stamp_ms": diagnostic_uint(world.get("stamp_ms")),
            "mode": diagnostic_enum(status.get("mode"), DIAGNOSTIC_MODES),
            "status_stop": diagnostic_stop(status.get("stop")),
            "vla_armed": "vla" in armed if isinstance(armed, list) else None,
            "active_present": status["active"] is not None if "active" in status else None,
            "world_age_ms": diagnostic_uint(status.get("world_age_ms")),
            "active_expires_ms": diagnostic_uint(status.get("active_expires_ms")),
            "recorder_ok": status.get("recorder_ok") if type(status.get("recorder_ok")) is bool else None,
            "state_ok": status.get("state_ok") if type(status.get("state_ok")) is bool else None,
            "arm_cancelling": status.get("arm_cancelling") if type(status.get("arm_cancelling")) is bool else None}


class BenchGate:
    def __init__(self, binary):
        self.temp = tempfile.TemporaryDirectory(prefix="haetae-led-")
        root = Path(self.temp.name)
        root.chmod(0o700)
        self.bridge = None
        self.last_approved_cycle = None
        self.last_cycle = None
        self._diagnostic_cycle = None
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
        request_ms = now_ms()
        started_ns = time.monotonic_ns() if self._diagnostic_cycle is not None else None
        step = self.bridge.request({"t": request_ms, "k": "signed", "data": json.dumps(envelope)})
        if self._diagnostic_cycle is not None and len(self._diagnostic_cycle["responses"]) < 2:
            completed_ns = time.monotonic_ns()
            elapsed_ms = (time.monotonic() - self._diagnostic_started) * 1000
            self._diagnostic_cycle["responses"].append(
                diagnostic_response(step, self._diagnostic_phase, request_ms, elapsed_ms, started_ns, completed_ns))
        return step

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
        self.last_cycle = {"result": "incomplete", "responses": []}
        self._diagnostic_cycle, self._diagnostic_started = self.last_cycle, started
        try:
            if fault != "world-loss":
                self._diagnostic_phase = "world"
                step = self.world(fault == "person")
                if step.get("stop") not in (None, "no_command", "startup") or step.get("outcome", {}).get("rejected"):
                    self.last_cycle["result"] = "denied"
                    return False
            self._diagnostic_phase = "motion"
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
                self.last_cycle["result"] = "approved"
                self.last_approved_cycle = self.last_cycle
                return True
            self.last_cycle["result"] = "denied"
            return False
        finally:
            self._diagnostic_cycle = None

    def denial_diagnostics(self):
        """Latest approval and current cycle only; denial may have cleared the Rust active expiry."""
        cycles = [self.last_approved_cycle] if self.last_approved_cycle is not None else []
        if self.last_cycle is not None and self.last_cycle is not self.last_approved_cycle:
            cycles.append(self.last_cycle)
        return cycles

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
