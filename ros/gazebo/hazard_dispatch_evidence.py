"""Attribute a household denial to one exact proposal in the protected log.

This is diagnostic correlation, not a replacement for sillok verification.
Missing or ambiguous records never establish a proposal identity.
"""

import json
import math

from safe_evidence import read_evidence


_ENTRY_FIELDS = {"seq", "ts_ms", "kind", "payload", "prev", "hash"}
_BINDING_FIELDS = {"schema_version", "world_revision", "task_revision", "task_id",
                   "step_id", "robot_id", "model_sha256", "tool_id", "item_id"}
# A full household run is a few MiB. Diagnostic lookup must not consume the
# motion wait budget parsing an unusually large log; unknown keeps its timeout.
MAX_DISPATCH_LOG_BYTES = 8 * 1024 * 1024
MAX_DISPATCH_LOG_RECORDS = 20_000


def _uint(value):
    return type(value) is int and 0 <= value <= 2**64 - 1


def _hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and all(char in "0123456789abcdef" for char in value))


def _same_json(left, right):
    # Rust serializes positions as floats; an integer input position is equal.
    # Booleans must not compare equal to numeric revisions, times or positions.
    if type(left) in (int, float) and type(right) in (int, float):
        return ((type(left) is int or math.isfinite(left))
                and (type(right) is int or math.isfinite(right)) and left == right)
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("nonfinite JSON number")


def dispatch_denial(path, *, root, expected_uid, points, binding, sent_sim_ms):
    """Return bounded exact-denial evidence, or an unknown correlation reason.

    The caller keeps its existing timeout when this returns unknown. The source
    clock may lag the dispatch clock; that also leaves attribution unknown.
    """
    def unknown(reason):
        return {"correlation": "unknown", "reason": reason}

    if (not _uint(sent_sim_ms) or not isinstance(binding, dict)
            or set(binding) != _BINDING_FIELDS):
        return unknown("invalid_dispatch")
    expected_action = {"type": "joint_trajectory", "ttl_ms": 1000,
                       "points": [{"time_from_start_ms": millis, "positions": list(values)}
                                  for millis, values in points]}
    try:
        content = read_evidence(root, path, expected_uid,
                                max_bytes=MAX_DISPATCH_LOG_BYTES).decode("utf-8")
    except (OSError, ValueError):
        return unknown("log_unavailable")
    if content and not content.endswith("\n"):
        return unknown("partial_log")
    if content.count("\n") > MAX_DISPATCH_LOG_RECORDS:
        return unknown("log_record_limit")
    records = []
    previous_hash = "0" * 64
    previous_time = 0
    try:
        for seq, line in enumerate(content.splitlines()):
            row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_bad_constant)
            if (not isinstance(row, dict) or set(row) != _ENTRY_FIELDS
                    or not _uint(row["seq"]) or row["seq"] != seq
                    or not _uint(row["ts_ms"]) or not isinstance(row["kind"], str)
                    or not row["kind"] or not isinstance(row["payload"], dict)
                    or not _hex(row["hash"], 64) or row["prev"] != previous_hash):
                return unknown("malformed_log")
            previous_hash = row["hash"]
            if row["kind"] == "seal":
                payload = row["payload"]
                if (set(payload) != {"sig", "key_id"} or not _hex(payload["sig"], 128)
                        or not _hex(payload["key_id"], 16)):
                    return unknown("malformed_log")
                continue
            if row["ts_ms"] < previous_time:
                return unknown("malformed_log")
            previous_time = row["ts_ms"]
            records.append(row)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return unknown("malformed_log")

    matches = []
    for index, row in enumerate(records):
        if row["kind"] != "proposal":
            continue
        proposal = row["payload"].get("proposal")
        if (not isinstance(proposal, dict)
                or not {"id", "source", "timestamp_ms", "action"} <= proposal.keys()
                or not _uint(proposal["id"]) or not _uint(proposal["timestamp_ms"])):
            return unknown("malformed_log")
        if (row["ts_ms"] >= sent_sim_ms and proposal["timestamp_ms"] >= sent_sim_ms
                and proposal["source"] == "vla"
                and _same_json(proposal.get("semantic"), binding)
                and _same_json(proposal["action"], expected_action)):
            matches.append(index)
    if not matches:
        return unknown("no_matching_proposal")
    if len(matches) != 1:
        return unknown("ambiguous_proposal")
    index = matches[0]
    proposal_row = records[index]
    proposal = proposal_row["payload"]["proposal"]
    if index + 1 == len(records):
        return unknown("missing_decision")
    decision_row = records[index + 1]
    decision = decision_row["payload"]
    if (decision_row["kind"] != "decision" or decision_row["ts_ms"] != proposal_row["ts_ms"]
            or not _uint(decision.get("proposal_id")) or decision["proposal_id"] != proposal["id"]):
        return unknown("mismatched_decision")
    if decision.get("verdict") != "bul":
        return unknown("no_matching_denial")
    fired = decision.get("fired")
    if (decision.get("action", False) is not None or not isinstance(fired, list)
            or not 1 <= len(fired) <= 64
            or any(not isinstance(reason, str) or not 1 <= len(reason) <= 256 for reason in fired)):
        return unknown("malformed_decision")
    return {"correlation": "exact", "reason": "matching_proposal_denied",
            "proposal_id": proposal["id"], "fired": fired,
            "proposal_seq": proposal_row["seq"], "decision_seq": decision_row["seq"],
            "ts_ms": proposal_row["ts_ms"], "proposal_timestamp_ms": proposal["timestamp_ms"]}
