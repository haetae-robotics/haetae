"""Bounded, scalar-only diagnostics for the household test harness."""

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class ObservationSnapshot:
    sim_ms: int
    wall: float
    # Copy source timestamps, never retain mutable ROS messages or fixture poses.
    sources: tuple

    def report(self):
        return {
            "now_sim_ms": self.sim_ms,
            "now_wall": self.wall,
            "sources": {
                name: {
                    "stamp_ms": stamp,
                    "received_wall": received,
                    "age_sim_ms": self.sim_ms - stamp if stamp is not None else None,
                    "age_wall_ms": (self.wall - received) * 1000 if received is not None else None,
                }
                for name, stamp, received in self.sources
            },
        }


def observation_snapshot(sim_ms, wall, target, item, joint, odom,
                         joint_received, odom_received):
    def stamp(message):
        return (message.header.stamp.sec * 1000 + message.header.stamp.nanosec // 1_000_000
                if message is not None else None)

    return ObservationSnapshot(sim_ms, wall, (
        ("target", target[1] if target else None, target[2] if target else None),
        ("item", item[1] if item else None, item[2] if item else None),
        ("joint", stamp(joint), joint_received),
        ("odom", stamp(odom), odom_received),
    ))


class ObservationUnavailable(RuntimeError):
    def __init__(self, reasons, snapshot):
        super().__init__("household observation unavailable: " + ", ".join(reasons))
        self.reasons = tuple(reasons)
        self.snapshot = snapshot


class OmissionHistory:
    def __init__(self):
        self._lock = Lock()
        self.count = 0
        self.rows = deque(maxlen=32)

    def record(self, reasons, snapshot, *, case, task_revision, step_id, fused_stamp_ms):
        row = (tuple(reasons), snapshot, case, task_revision, step_id, fused_stamp_ms)
        with self._lock:
            self.count += 1
            self.rows.append(row)

    def report(self):
        # The ROS callback may append while the failure report is rendered.
        # Copy count and immutable rows together, then release it before rendering.
        with self._lock:
            count, limit, rows = self.count, self.rows.maxlen, tuple(self.rows)
        return {"total": count, "retained_limit": limit, "recent": [
            {"reasons": list(reasons), "observation": snapshot.report(), "case": case,
             "task_revision": task_revision, "step_id": step_id, "fused_stamp_ms": fused}
            for reasons, snapshot, case, task_revision, step_id, fused in rows
        ]}


def outcome_summary(row):
    """Only fixed diagnostic fields; reject errors do not carry proposal identity."""
    def text(value):
        return value[:256] if isinstance(value, str) else None

    decision = row.get("decision", {})
    error = row.get("rejected", {}).get("error")
    identity = decision.get("proposal_id")
    fired = decision.get("fired")
    return {"proposal_id": identity if type(identity) is int and 0 <= identity < 2**64 else None,
            "verdict": text(decision.get("verdict")),
            "fired": [text(reason) for reason in fired[:32]] if isinstance(fired, list) else None,
            "rejected_error": text(error), "rejected_correlation": "unknown" if error else None}


class DispatchWatch:
    """Inspect at most three new denial notifications during one existing wait."""
    def __init__(self, evidence, *, points, binding, sent_sim_ms, sent_wall):
        self.evidence = evidence
        self.points = tuple((ms, tuple(values)) for ms, values in points)
        self.binding = dict(binding)
        self.sent_sim_ms = sent_sim_ms
        self.sent_wall = sent_wall
        self.seen = set()
        self.reads = 0
        self.result = {"correlation": "unknown", "reason": "no_denial_evidence_checked"}

    def check(self, outcomes):
        for received, row in outcomes:
            decision = row.get("decision", {})
            identity = decision.get("proposal_id")
            if (received < self.sent_wall or decision.get("verdict") != "bul"
                    or type(identity) is not int or identity in self.seen):
                continue
            if self.reads >= 3 or self.evidence is None:
                continue
            self.seen.add(identity)
            self.reads += 1
            self.result = self.evidence(points=self.points, binding=self.binding,
                                        sent_sim_ms=self.sent_sim_ms)
            if self.result.get("correlation") == "exact":
                raise RuntimeError(
                    "household dispatched proposal denied: "
                    f"proposal_id={self.result['proposal_id']} fired={self.result['fired']}"
                )
