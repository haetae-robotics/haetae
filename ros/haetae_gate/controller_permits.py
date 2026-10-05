"""Exact-action permits for the same-host Gazebo controller reference.

The trusted Rust owner signs; the relay never receives a signing key. Wire
integers and IEEE-754 payloads are canonical and shared with permit.hpp.
"""
import hashlib
import math
import struct
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DOMAIN = b"haetae-controller-v1\0"
IDLE = "0" * 64
MAX_LEASE_NS = 200_000_000


class AcceptedWorldClock:
    """Wall deadline for a strictly advancing, Rust-accepted observation.

    A wall-driven permit tick cannot extend the life of a frozen ROS world.
    The wall anchor is the original request start, not response/signing time.
    """
    def __init__(self):
        self.stamp_ns = None
        self.wall_ns = None

    def observe(self, step, request_wall_ns):
        updated = (step.get("outcome") or {}).get("world_updated") or {}
        stamp = updated.get("stamp_ms")
        if type(stamp) is int and stamp >= 0:
            stamp *= 1_000_000
            if self.stamp_ns is None or stamp > self.stamp_ns:
                self.stamp_ns, self.wall_ns = stamp, request_wall_ns

    def remaining(self, sim_ns, wall_ns, maximum_ns):
        if (self.stamp_ns is None or self.wall_ns is None or
                sim_ns < self.stamp_ns or wall_ns < self.wall_ns):
            return 0
        return max(0, min(maximum_ns - (sim_ns - self.stamp_ns),
                          maximum_ns - (wall_ns - self.wall_ns)))


def stamp_ns(stamp):
    if not 0 <= stamp.sec < 2**31 or not 0 <= stamp.nanosec < 1_000_000_000:
        raise ValueError("invalid ROS timestamp")
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def base_digest(command):
    t = command.twist
    values = (t.linear.x, t.linear.y, t.linear.z, t.angular.x, t.angular.y, t.angular.z)
    if not all(math.isfinite(v) for v in values) or any(values[i] != 0 for i in (1, 2, 3, 4)):
        raise ValueError("unsupported base command")
    return hashlib.sha256(b"base\0" + struct.pack(">Q6d", stamp_ns(command.header.stamp), *values)).hexdigest()


def arm_digest(trajectory):
    names = trajectory.joint_names
    if names != ["joint1", "joint2", "joint3", "joint4"] or not 1 <= len(trajectory.points) <= 16:
        raise ValueError("unsupported arm joints or point count")
    raw = b"arm\0" + struct.pack(">Q", stamp_ns(trajectory.header.stamp))
    raw += struct.pack(">I", len(names))
    for name in names:
        encoded = name.encode("ascii")
        raw += struct.pack(">I", len(encoded)) + encoded
    raw += struct.pack(">I", len(trajectory.points))
    last = -1
    for point in trajectory.points:
        duration = stamp_ns(point.time_from_start)
        if (len(point.positions) != 4 or not all(math.isfinite(v) for v in point.positions)
                or point.velocities or point.accelerations or point.effort
                or not last < duration <= 1_000_000_000):
            raise ValueError("unsupported arm point")
        raw += struct.pack(">Q4d", duration, *point.positions)
        last = duration
    return hashlib.sha256(raw).hexdigest()


def explicit_rearm(step):
    status = step.get("status") or {}
    decision = (step.get("outcome") or {}).get("decision") or {}
    return (decision.get("action") == {"type": "stop"} and decision.get("verdict") == "yun"
            and bool(status.get("armed")) and status.get("active") is None
            and status.get("mode") == "normal" and not status.get("arm_cancelling"))


class PermitSigner:
    def __init__(self, key_path):
        self.key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_path.read_text().strip()))
        self.challenges = {}
        self.sequence = {"base": 0, "arm": 0}
        self.active_arm = IDLE
        self.admitted_arm = IDLE
        self.goal_sequence = 0
        self.admitted_goal_sequence = 0

    def observe(self, target, value):
        nonce = value.get("nonce", "")
        if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
            raise ValueError("invalid controller nonce")
        self.challenges[target] = nonce
        if target == "arm":
            digest = value.get("active_digest", IDLE)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid admitted arm digest")
            self.admitted_arm = digest
            sequence = value.get("goal_sequence", 0)
            if type(sequence) is not int or not 0 <= sequence < 2**63:
                raise ValueError("invalid admitted goal sequence")
            self.admitted_goal_sequence = sequence

    def sign(self, target, kind, digest, sim_ns, remaining_ns=MAX_LEASE_NS, wall_ns=None):
        if target not in self.challenges:
            raise ValueError("controller challenge unavailable")
        remaining_ns = min(MAX_LEASE_NS, int(remaining_ns))
        if remaining_ns <= 0:
            raise ValueError("controller authority expired")
        self.sequence[target] += 1
        wall_ns = time.monotonic_ns() if wall_ns is None else wall_ns
        body = ":".join(("v1", target + "-" + kind, self.challenges[target],
                         str(self.sequence[target]), str(sim_ns), str(wall_ns),
                         str(sim_ns + remaining_ns), str(wall_ns + remaining_ns), digest))
        return body + ":" + self.key.sign(DOMAIN + body.encode("ascii")).hex()
