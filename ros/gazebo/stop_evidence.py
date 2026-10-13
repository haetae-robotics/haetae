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
    if any(t >= since and row.get("stop") == "denied" and not row.get("armed")
           for t, row in states):
        for error, reason in (
                ("stale actuation response: world expired", "world_expired_at_response"),
                ("trusted world did not advance within dual-clock freshness", "world_expired_at_owner")):
            if any(t >= since and row.get("rejected", {}).get("error") == error for t, row in outcomes):
                return reason
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
        # A controller cannot receive a useful grant in the last 50ms of
        # original world authority. This named owner rejection identifies
        # that cutoff separately from proposal TTL or generic response age.
        # Require disarm and a subsequent observed zero after the rejection;
        # coverage loss still requires the signed perception-unknown path.
        error = "trusted world has insufficient controller admission budget"
        for rejected_at, row in list(outcomes):
            if rejected_at < since or row.get("rejected", {}).get("error") != error:
                continue
            disarmed = any(t >= rejected_at and state.get("stop") in ("denied", "unarmed")
                           and not state.get("armed") and state.get("active") is None
                           for t, state in states)
            zero_at = next((wall for wall, _ in zeros if wall >= rejected_at), None)
            if disarmed and zero_at is not None:
                return {"stop_reason": "world_admission_budget_exhausted", "zero_at": zero_at}
        reason = world_expiry_stop_observed(states, outcomes, since)
        zero_at = next((wall for wall, _ in zeros if wall >= since), None)
        if reason and zero_at is not None:
            return {"stop_reason": reason, "zero_at": zero_at}
    return None


def motion_after_stop(states, commands, since):
    """Commands above 0.03 m/s received after the gate's first stop since `since`.

    The stop is the first state since `since` with nothing armed. The engine clears
    every armed source on each rejection and on each denied, revoked, unarmed or
    stale_world stop (crates/haetae-enforce/src/lib.rs), every engine step includes
    its state, so each stop has a disarmed state, and only an explicit zero command
    arms a source again; the fault phases never send one. The gate publishes the
    state after the stop's zero (ros/haetae_gate/node.py `_publish`), but state and
    relayed-command callbacks have no shared publication-order marker. This is
    therefore a conservative receive-order check, not proof of when a command
    was emitted. Every observed motion command after the state is returned:
    the harness cannot distinguish delayed pre-stop motion from new post-stop
    motion and exempts neither. Equal receive timestamps are also ambiguous
    and count as after the stop. No timing threshold is involved. Rejection
    outcomes cannot replace the state marker: ticks publish states without
    outcomes.
    """
    stop = next((t for t, row in list(states) if t >= since and not row.get("armed")), None)
    if stop is None:
        return []
    return [(t, value) for t, value in list(commands) if t >= stop and abs(value) > 0.03]


# Gate rejections that end the trusted world's authority: ros/haetae_gate/node.py
# _permit ("insufficient controller admission budget") and _receive ("did not
# advance within dual-clock freshness"), and bridge.require_fresh_actuation
# ("world expired"). The request-age rejection "original controller authority has
# insufficient admission budget" is not one.
WORLD_AUTHORITY_CUTS = ("trusted world has insufficient controller admission budget",
                        "trusted world did not advance within dual-clock freshness",
                        "stale actuation response: world expired")


def coverage_cut_record(log_path, since_ms, cut_error, unknown_stamp, *, root=None, expected_uid=None):
    """Correlate the first authority loss in the engine's own record order.

    Unlike ROS callback order, this includes tick-only stops. A named reject
    must precede its own Denied stop, at the same engine timestamp, and the
    confidence-zero world must come later. A partial final record cannot prove
    this sequence. Expired and NoCommand keep the source armed; other stops do
    not establish the required named cause and are conservatively rejected.
    """
    if type(since_ms) is not int:
        return False
    cut_ms = None
    denied = False
    complete = False
    for line in read_evidence_text(root or log_path.parent, log_path, expected_uid).splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(row, dict):
            return False
        stamp, kind, payload = row.get("ts_ms"), row.get("kind"), row.get("payload")
        if type(stamp) is not int or not isinstance(payload, dict):
            return False
        # SillokWriter may append an automatic seal between any two engine
        # records. Its wall-clock timestamp is not an engine event or a stop.
        if kind == "seal" or stamp < since_ms or complete:
            continue
        if cut_ms is None:
            if kind == "reject":
                if payload.get("error") != cut_error:
                    return False
                cut_ms = stamp
            elif (kind == "revoke" or kind == "decision" and payload.get("verdict") == "bul"
                  or kind == "world" and payload.get("confidence") == 0
                  or kind == "stop" and payload.get("reason") not in ("Expired", "NoCommand")):
                return False
        elif not denied:
            # reject() records its stop synchronously before another engine input.
            if kind != "stop" or payload.get("reason") != "Denied" or stamp != cut_ms:
                return False
            denied = True
        elif kind == "decision" and payload.get("verdict") == "yun":
            return False  # No explicit rearm or newly allowed action belongs in this fault window.
        elif kind == "world" and payload.get("confidence") == 0:
            if type(payload.get("stamp_ms")) is not int or payload["stamp_ms"] != unknown_stamp or stamp < cut_ms:
                return False
            complete = True
    return complete


def coverage_cut_first(log_path, states, outcomes, zeros, commands, published, since, since_ms,
                       *, root=None, expected_uid=None):
    """Evidence that a named world-authority cut, not the perception rule, stopped motion.

    Lost coverage passes only through sensor_stop_report(require_perception=True).
    On a slow runner the gate's dual-clock world authority can end first. That stop
    is fail-closed, but it shows nothing about the perception-unknown rule, so this
    evidence makes an attempt inconclusive, never a pass. All of these must hold;
    otherwise the result is None and the attempt fails.

    - The protected engine log records the named reject as the first authority
      loss after the fault, followed by its Denied stop and the unknown world.
      Earlier stops from ticks, revocations and other rejections give None.
    - The gate's outcome order corroborates this: any other rejection or a
      `bul` decision before the named cut gives None. ROS streams have no shared
      step identifier, so callback adjacency alone cannot establish the cause.
    - The cut comes before every accepted world whose stamp the harness ever
      published with confidence 0, or never published (`published`: (stamp,
      confidence) of each published world).
    - A world the harness published only with confidence 0 is accepted after the cut.
    - The latest state arrived at or after the cut and is disarmed: stop denied
      or unarmed, nothing armed, no active proposal.
    - No motion command after the stop (motion_after_stop). A zero was received
      after the last motion command and after the cut's outcome. The first zero
      after the last motion command only anchors the recorded fault-to-zero times.
    """
    published = list(published)
    maybe_unknown = {stamp for stamp, confidence in published if confidence == 0}
    unknown = maybe_unknown - {stamp for stamp, confidence in published if confidence != 0}
    recorded = {stamp for stamp, _ in published}
    cut = accepted = last_world = None
    for at, row in list(outcomes):
        if at < since:
            continue
        updated, rejected, decision = row.get("world_updated"), row.get("rejected"), row.get("decision")
        stamp = updated.get("stamp_ms") if isinstance(updated, dict) else None
        stamp = stamp if type(stamp) is int else None
        if cut is None:
            if (stamp is not None and (stamp in maybe_unknown or stamp not in recorded)
                    or isinstance(decision, dict) and decision.get("verdict") == "bul"):
                return None  # The perception rule had to judge this world, or another cause came first.
            if isinstance(rejected, dict):
                if rejected.get("error") not in WORLD_AUTHORITY_CUTS:
                    return None
                cut = (at, rejected["error"])
            elif stamp is not None:
                last_world = stamp
        elif stamp in unknown:
            accepted = (at, stamp)
            break
    states = list(states)
    if accepted is None or not states or states[-1][0] < cut[0]:
        return None
    latest = states[-1][1]
    if not (latest.get("stop") in ("denied", "unarmed") and latest.get("armed") == []
            and "active" in latest and latest["active"] is None):
        return None
    if not coverage_cut_record(log_path, since_ms, cut[1], accepted[1], root=root, expected_uid=expected_uid):
        return None
    # A contradictory observed stop cannot be hidden by a later named rejection.
    first_disarmed = next(((at, row) for at, row in states if at >= since and not row.get("armed")), None)
    if first_disarmed is None or first_disarmed[1].get("stop") != "denied":
        return None
    if any(first_disarmed[0] < at < cut[0] and ("world_updated" in row or "decision" in row)
           for at, row in list(outcomes)):
        return None
    commands, zeros = list(commands), list(zeros)
    if motion_after_stop(states, commands, since):
        return None
    last_motion = max((t for t, value in commands if t >= since and abs(value) > 0.03), default=since)
    zero = next(((wall, sim) for wall, sim in zeros if wall > last_motion), None)
    if zero is None or not any(wall >= cut[0] for wall, _ in zeros):
        return None
    evidence = {"cause": "world_authority_cut_before_unknown_world", "cut": cut[1],
                "cut_after_fault_wall_ms": round((cut[0] - since) * 1000, 1),
                "unknown_world_after_fault_wall_ms": round((accepted[0] - since) * 1000, 1),
                "unknown_world_stamp_ms": accepted[1], "last_world_stamp_ms": last_world,
                "fault_to_zero_wall_ms": round((zero[0] - since) * 1000, 1)}
    # The same stop on the simulation clock. With the wall values these show the
    # real-time factor and the last world's age when the zero arrived.
    evidence.update(fault_sim_ms=since_ms, fault_to_zero_sim_ms=zero[1] - since_ms)
    return {"zero_at": zero[0], "inconclusive": evidence}
