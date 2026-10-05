"""Correlate engine revocations with their observed worlds and ROS commands."""

import json
from safe_evidence import read_evidence_text


def person_stop_report(log_path, reports, since_ms, *, root=None, expected_uid=None):
    """Return the sender's world sample that caused the first person verdict.

    World-triggered revocation is recorded in the engine log, but does not
    produce a proposal Decision outcome. Match the log's world stamp instead.
    Ignore a partially written final line while the engine appends.
    """
    samples = {row["stamp_ms"]: row for row in list(reports)}
    last_world = None
    for line in read_evidence_text(root or log_path.parent, log_path, expected_uid).splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            break
        if row["kind"] == "world":
            last_world = row["payload"]
        if (last_world and last_world["stamp_ms"] >= since_ms and
                "person" in row["payload"].get("fired", [])):
            return samples.get(last_world["stamp_ms"])
    return None


def person_stop_observed(log_path, reports, zeros, since_ms, **boundary):
    """Require both a person verdict and a zero command after its world sample."""
    trigger = person_stop_report(log_path, reports, since_ms, **boundary)
    return trigger is not None and any(wall >= trigger["wall"] for wall, _ in list(zeros))
def world_expiry_stop_observed(states, outcomes, since):
    """Accept either engine-age revocation or a timely response-boundary reject."""
    if any(t >= since and row.get("stop") == "stale_world" for t, row in states):
        return "stale_world"
    if (any(t >= since and row.get("stop") == "denied" and not row.get("armed")
            for t, row in states) and any(t >= since and row.get("rejected", {}).get("error") ==
                "stale actuation response: world expired" for t, row in outcomes)):
        return "world_expired_at_response"
    return None


def sensor_stop_report(log_path, states, outcomes, zeros, since, since_ms,
                       *, require_perception=False, root=None, expected_uid=None):
    """Prove a named Rust revocation of a signed unknown-perception world.

    The protected engine log records only accepted worlds. A generic revoked
    status or a zero from an earlier expiry is insufficient for this cause.
    When the observer/signing path disappears entirely, retain the original
    world-expiry oracle. Coverage loss must require the perception path.
    """
    states, zeros = list(states), list(zeros)
    if any(t >= since and row.get("stop") == "revoked" and not row.get("armed")
           for t, row in states):
        last_world = None
        last_world_at = None
        for line in read_evidence_text(root or log_path.parent, log_path, expected_uid).splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                break
            if row["kind"] == "world":
                last_world = row["payload"]
                last_world_at = row["ts_ms"]
            if (row["kind"] == "revoke" and last_world and
                    last_world_at is not None and last_world_at >= since_ms and
                    last_world.get("confidence") == 0 and
                    "perception-unknown" in row["payload"].get("fired", [])):
                zero_at = next((wall for wall, stamp in zeros
                                if wall >= since and stamp >= row["ts_ms"]), None)
                if zero_at is not None:
                    return {"stop_reason": "perception_unknown", "zero_at": zero_at,
                            "world_stamp_ms": last_world["stamp_ms"]}
    if not require_perception:
        reason = world_expiry_stop_observed(states, outcomes, since)
        zero_at = next((wall for wall, _ in zeros if wall >= since), None)
        if reason and zero_at is not None:
            return {"stop_reason": reason, "zero_at": zero_at}
    return None
