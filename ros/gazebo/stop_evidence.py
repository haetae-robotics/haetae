"""Correlate a person-triggered engine stop with measured ROS commands."""

import json


def person_stop_report(log_path, reports, since_ms):
    """Return the sender's world sample that caused the first person verdict.

    World-triggered revocation is recorded in the engine log, but does not
    produce a proposal Decision outcome. Match the log's world stamp instead.
    Ignore a partially written final line while the engine appends.
    """
    samples = {row["stamp_ms"]: row for row in list(reports)}
    last_world = None
    for line in log_path.read_text().splitlines():
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


def person_stop_observed(log_path, reports, zeros, since_ms):
    """Require both a person verdict and a zero command after its world sample."""
    trigger = person_stop_report(log_path, reports, since_ms)
    return trigger is not None and any(wall >= trigger["wall"] for wall, _ in list(zeros))
