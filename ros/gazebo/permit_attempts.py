"""Pass / fail / inconclusive rules for the Gazebo controller-permit probes.

No ROS imports, so every rule here is unit tested on any host. Shared-runner
delay may make one attempt inconclusive; it never makes a case pass. Only
RunnerDelay leads to another attempt, each from a fresh maintenance reset, up
to MAX_ATTEMPTS attempts in total; any other exception fails the case at once.
The 50 ms bound mirrors the verifier's freshness predicate (permit.hpp:146-147)
and is never tuned here; the 50 ms and 200 ms controller limits are unchanged.

When the controller refuses a permit that was timely when it left, only two
outcomes are inconclusive. One is the verifier's own `freshness` refusal. The
other is a lock (`expired`, `locked`, or `rejected` at the arm action ingress)
that the controller's own telemetry places at or after the instant a signed
time bound could first close: the end of the lease the controller relied on,
or, for an arm goal refused after admission, that goal's 50 ms window. Any
other reason, any lock before such a bound, and any admission of a packet that
must be refused fail at once. The same rule judges a lapse that the controller
published before a negative packet's refusal, and any lapse already visible
when the harness itself is delayed before a packet leaves.
"""
import contextlib
import json
import math
import sys
import time

from public_report import (CONTROLLER_UPDATE_NS, DRIFT_LIMIT, HOLD_UPDATES, LAPSE_ALLOWANCE_NS,
                           PERMIT_MAX_ATTEMPTS, VERIFIER_WINDOW_NS)
from safe_evidence import write_checkpoint

MAX_ATTEMPTS = PERMIT_MAX_ATTEMPTS
TIMING_FILE = "controller-timing.json"
MAX_SERIES_SAMPLES = 4096
UPDATE_MS = CONTROLLER_UPDATE_NS // 1_000_000
PENDING = "pending"


def public(value):
    """Bounded, JSON-safe copy of evidence values (finite numbers, short strings)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3) if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, dict):
        return {str(key)[:80]: public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(item) for item in value[:50]]
    return str(value)[:300]


class RunnerDelay(Exception):
    """Shared-runner delay made this attempt inconclusive. Never a pass.

    Deliberately not an AssertionError, TimeoutError or RuntimeError subclass:
    only bounded_attempts() catches it, and anywhere else it fails the run.
    """

    def __init__(self, stage, evidence=None):
        self.stage = str(stage)
        self.evidence = public(dict(evidence or {}))
        super().__init__(self.stage + ": " + json.dumps(self.evidence, sort_keys=True))


def permit_fields(token):
    """Public wire fields of a v1 controller permit (controller_permits.py:137-141)."""
    fields = token.split(":") if isinstance(token, str) else []
    if len(fields) != 10 or fields[0] != "v1":
        raise ValueError("unexpected controller permit wire format")
    seq, sim, wall, sim_end, wall_end = (int(value) for value in fields[3:8])
    return {"kind": fields[1], "seq": seq, "sim_ns": sim, "wall_ns": wall,
            "sim_end_ns": sim_end, "wall_end_ns": wall_end}


def permit_age_ns(fields, wall_ns, sim_ns):
    """Age the verifier computes from the signed origins (sim origin is backdated)."""
    return max(wall_ns - fields["wall_ns"], sim_ns - fields["sim_ns"])


def check_timely(stage, fields, wall_ns, sim_ns):
    """The verifier's freshness predicate at the instant the permit leaves.

    Uses the signed (backdated) simulation origin, as permit.hpp does. A permit
    the verifier must already refuse is never sent; the attempt is inconclusive.
    Returns the age in ms.
    """
    age = permit_age_ns(fields, wall_ns, sim_ns)
    if (age >= VERIFIER_WINDOW_NS or wall_ns >= fields["wall_end_ns"]
            or sim_ns >= fields["sim_end_ns"]):
        raise RunnerDelay(stage, {"cause": "permit_aged_before_send", "age_ms": age / 1e6,
                                  "wall_age_ms": (wall_ns - fields["wall_ns"]) / 1e6,
                                  "signed_sim_age_ms": (sim_ns - fields["sim_ns"]) / 1e6})
    return age / 1e6


def require_live(stage, grant, wall_ns, sim_ns):
    """The grant that keeps the controller unlocked must still be live at send."""
    if wall_ns >= grant["wall_end_ns"] or sim_ns >= grant["sim_end_ns"]:
        raise RunnerDelay(stage, {"cause": "governing_lease_ended_before_send",
                                  "wall_past_end_ms": (wall_ns - grant["wall_end_ns"]) / 1e6,
                                  "sim_past_end_ms": (sim_ns - grant["sim_end_ns"]) / 1e6})


def require_fresh_witness(stage, published_ns, sent_ns):
    """public_report.negative_permit_rejected requires pre-send telemetry under 50 ms old."""
    age = sent_ns - published_ns
    if not 0 <= age < VERIFIER_WINDOW_NS:
        raise RunnerDelay(stage, {"cause": "pre_send_telemetry_aged", "telemetry_age_ms": age / 1e6})


def require_stale_window(stage, fields, wall_ns, sim_ns):
    """A deliberately stale permit leaves refusable only by the verifier's 50 ms age bound.

    Its signed origins are at least 50 ms old on both clocks, and its own ends
    are still ahead, so permit.hpp:148 (ends already passed) cannot refuse it.
    """
    if (wall_ns - fields["wall_ns"] < VERIFIER_WINDOW_NS
            or sim_ns - fields["sim_ns"] < VERIFIER_WINDOW_NS):
        raise AssertionError(stage + ": deliberately stale permit is under 50 ms old at send")
    if wall_ns >= fields["wall_end_ns"] or sim_ns >= fields["sim_end_ns"]:
        raise RunnerDelay(stage, {"cause": "stale_permit_ends_passed_before_send",
                                  "wall_to_end_ms": (fields["wall_end_ns"] - wall_ns) / 1e6,
                                  "sim_to_end_ms": (fields["sim_end_ns"] - sim_ns) / 1e6})


def require_refused_inside_stale_window(stage, refusal_row, fields):
    """public_report.stale_permit_refused_in_window: the refusal is visible before the
    stale permit's own ends could close, so the end clause cannot explain it."""
    published = refusal_row.get("published_wall_ns", 0)
    if published >= fields["wall_end_ns"] - LAPSE_ALLOWANCE_NS:
        raise RunnerDelay(stage, {"cause": "refusal_not_separable_from_stale_permit_end",
                                  "published_to_end_ms": (fields["wall_end_ns"] - published) / 1e6})


def require_refused_inside_lease(target, stage, refusal_row, lease_wall_end_ns):
    """public_report.refused_inside_lease: no lapse of the lease the controller relied on
    can have come before the refusal.

    accept() lapses an ended lease before it checks the packet, then overwrites
    `expired` with the refusal's reason (permit.hpp:132, 172-175), so a refusal that
    followed a lapse still looks latched. The arm's own hold stamps separate the two
    (refusal()); its refusal must still be published before the wall end. The base
    has no hold stamps and publishes every 20 ms, so its refusal counts only when
    published more than LAPSE_ALLOWANCE_NS before the signed wall end.
    """
    published = refusal_row.get("published_wall_ns", 0)
    allowance = LAPSE_ALLOWANCE_NS if target == "base" else 0
    if published >= lease_wall_end_ns - allowance:
        raise RunnerDelay(stage, {"cause": "rejection_not_separable_from_lease_end",
                                  "published_to_wall_end_ms": (lease_wall_end_ns - published) / 1e6,
                                  "allowance_ms": allowance / 1e6})


class GrantChain:
    """Signed grants this harness sent one controller since its reset, in send order.

    A positive permit leaves only after the controller counted the previous one
    (in_flight), so the controller's own `accepted` counter names exactly the
    grant it relied on in any telemetry row.
    """

    def __init__(self, first, accepted_before):
        self.grants = [first]
        self.accepted_before = accepted_before

    def sent(self, fields):
        self.grants.append(fields)

    def last(self):
        return self.grants[-1]

    def in_flight(self, accepted):
        """Whether a permit this harness sent is not yet admitted per the controller's counter.

        Positive permits leave one at a time, so two refusals never share one
        telemetry row and hide the first reason behind the second.
        """
        return type(accepted) is not int or accepted < self.accepted_before + len(self.grants)

    def admitted(self, accepted, stage):
        """(index, grant) of the last admitted grant; (-1, None) before the first."""
        index = accepted - self.accepted_before - 1 if type(accepted) is int else None
        if index is None or not -1 <= index < len(self.grants):
            raise AssertionError(stage + ": controller admission count " + str(accepted)
                                 + " does not match the " + str(len(self.grants))
                                 + " permits this harness sent")
        return index, self.grants[index] if index >= 0 else None


def first_fail_closed(rows, since_ns, rejected_before):
    """(index, row) of the first counted rejection or lease lapse published at/after since_ns."""
    for index, row in enumerate(rows):
        if row.get("published_wall_ns", 0) >= since_ns and (
                row.get("rejected", 0) > rejected_before or row.get("reason") == "expired"):
            return index, row
    return None, None


def hold_row(rows, after_ns):
    """First arm row carrying a hold transition stamped at or after after_ns.

    The arm controller stores stop_wall_ns (wall clock) and cutoff_ms (its
    simulation update time) at its first update after the guard locks, so these
    stamps are at or after the lock on each clock (controller.cpp:244-249).
    """
    for row in rows:
        stop, cutoff = row.get("stop_wall_ns"), row.get("cutoff_ms")
        if (row.get("holding") is True and type(stop) is int and type(cutoff) is int
                and stop >= after_ns):
            return row
    return None


def hold_evidence(rows, event_index, after_ns, stage):
    """The arm's hold after the event at rows[event_index]; None until it is visible.

    The hold must begin within HOLD_UPDATES controller updates of the event row's
    own update stamp (stamp_ms, simulation clock). If the controller keeps
    updating past that without a hold, the arm kept actuating: that fails.
    """
    stamp = rows[event_index].get("stamp_ms")
    if type(stamp) is not int:
        raise AssertionError(stage + ": arm telemetry carries no controller update stamp")
    limit = stamp + HOLD_UPDATES * UPDATE_MS
    held = hold_row(rows, after_ns)
    if held is not None:
        if held["cutoff_ms"] > limit:
            raise AssertionError(stage + ": arm held at controller time " + str(held["cutoff_ms"])
                                 + " ms, more than " + str(HOLD_UPDATES) + " updates after the refusal row ("
                                 + str(stamp) + " ms)")
        return held
    latest = max([row["stamp_ms"] for row in rows[event_index:] if type(row.get("stamp_ms")) is int])
    if latest > limit:
        raise AssertionError(stage + ": arm controller kept updating after the refusal without holding")
    return None


def hold_at_lease_end(held, grant):
    """Whether a lapse of `grant` can explain the arm hold `held`.

    The hold's own stamps must reach the grant's wall end, or its simulation end
    within one update (the controller may notice a lapse in a callback before its
    update).
    """
    return (held["stop_wall_ns"] >= grant["wall_end_ns"]
            or held["cutoff_ms"] * 1_000_000 >= grant["sim_end_ns"] - CONTROLLER_UPDATE_NS)


def lapse_evidence(target, rows, index, grant, after_ns, stage):
    """Whether the event at rows[index] can be the lapse of `grant`; None while pending.

    Base telemetry recomputes the lock when it publishes (every 20 ms) and has no
    simulation stamp, so its publication instant bounds the lapse. For the arm,
    its own hold stamps decide (hold_at_lease_end).
    """
    if grant is None:
        return False
    if target == "base":
        return rows[index].get("published_wall_ns", 0) >= grant["wall_end_ns"] - LAPSE_ALLOWANCE_NS
    held = hold_evidence(rows, index, after_ns, stage)
    if held is None:
        return None
    return hold_at_lease_end(held, grant)


def goal_window_evidence(rows, index, goal, after_ns, stage):
    """Whether an arm goal admitted by accept() and then refused at the action handshake
    (current_goal_stamp, permit.hpp:86-93) can have crossed its own 50 ms simulation
    window by the controller's hold; None while pending."""
    held = hold_evidence(rows, index, after_ns, stage)
    if held is None:
        return None
    return held["cutoff_ms"] * 1_000_000 >= goal["sim_ns"] + VERIFIER_WINDOW_NS - CONTROLLER_UPDATE_NS


def require_no_fail_closed(target, rows, since_ns, rejected_before, chain, hold_after_ns, stage):
    """Classify the first fail-closed event after since_ns in a positive case.

    Returns None when there is none and PENDING while the arm's hold evidence is
    not visible yet. Raises RunnerDelay for a `freshness` refusal or for a lock
    the evidence places at or after a signed bound (see the module docstring),
    and AssertionError for anything else.
    """
    index, row = first_fail_closed(rows, since_ns, rejected_before)
    if row is None:
        return None
    reason = row.get("reason")
    delta = row.get("rejected", 0) - rejected_before
    evidence = {"reason": reason, "rejected_delta": delta, "accepted": row.get("accepted"),
                "published_wall_ns": row.get("published_wall_ns")}
    if delta == 1 and reason == "freshness":
        raise RunnerDelay(stage, dict(evidence, cause="verifier_freshness_refusal"))
    lock = ((delta == 0 and reason == "expired") or (delta == 1 and reason == "locked")
            or (delta == 1 and reason == "rejected" and target == "arm"))
    if not lock:
        raise AssertionError(stage + ": timely permit rejected for " + str(reason) + " "
                             + json.dumps(public(evidence), sort_keys=True))
    position, grant = chain.admitted(row.get("accepted"), stage)
    if reason == "rejected" and grant is not None and grant["kind"] == "arm-goal":
        # accept() admitted (counted) this goal; the action handshake refused it.
        verdict = goal_window_evidence(rows, index, grant, hold_after_ns, stage)
        cause = "goal_window_closed_during_action_handshake"
    else:
        verdict = lapse_evidence(target, rows, index, grant, hold_after_ns, stage)
        cause = "governing_lease_lapsed"
    if verdict is None:
        return PENDING
    evidence.update(grant_index=position, grant_kind=grant and grant["kind"])
    if verdict:
        raise RunnerDelay(stage, dict(evidence, cause=cause))
    raise AssertionError(stage + ": controller locked (" + str(reason) + ") before any signed bound could close: "
                         + json.dumps(public(evidence), sort_keys=True))


def negative_timing(target, case):
    """Refusal reasons that runner delay can put in place of a negative case's expected one.

    permit.hpp checks binding, sequence, freshness, signature, then the lock, so
    only `signature` can be overtaken by `freshness`. The arm action ingress
    (goals only) refuses a lapsed lease with `rejected` before accept(); that
    still needs evidence the governing lease lapsed. Heartbeats have no such
    precheck, so a late renewal has no timing alternative.
    """
    reasons = ("freshness",) if case == "signature" else ()
    return reasons + (("rejected",) if target == "arm" and case != "renewal_delay" else ())


def refusal(target, rows, witness, expected, timing, governing, stage, hold_after_ns=None):
    """Judge controller telemetry after a packet that must be refused, oldest row first.

    witness: sent_wall_ns, nonce_before, accepted_before, rejected_before and, when
    the caller read one, before_published_wall_ns (its pre-send telemetry row).
    From that row on, only time may change until the refusal. A re-activation, an
    admission, or a rejection counted before the packet left fails before any
    timing is considered. A lapse (`expired`) of `governing`, the lease the
    controller relied on, is judged by lapse_evidence, as on the positive path:
    accept() lapses an ended lease and then overwrites `expired` with its own
    refusal reason (permit.hpp:132, 172-175), so the lapse is visible only before
    the refusal.
    Returns None while undecided, else (refusal_row, held_row): the first row with
    exactly one new refusal for `expected`, and the hold after it. A holding row
    must still show that refusal's reason (its latch) in every case. For the arm
    with hold_after_ns, held_row is its prompt hold transition stamped at or after
    hold_after_ns; if those stamps reach the end of `governing`, that lease's own
    lapse could explain the hold, so the attempt is inconclusive.
    A reason in `timing` is inconclusive only as `freshness`, or as `rejected` at
    the arm ingress with evidence that `governing` lapsed.
    """
    sent, before = witness["sent_wall_ns"], witness["rejected_before"]
    since = witness.get("before_published_wall_ns", sent)
    found = None
    for index, row in enumerate(rows):
        published = row.get("published_wall_ns", 0)
        if published < since:
            continue
        if row.get("nonce") != witness["nonce_before"] or row.get("accepted") != witness["accepted_before"]:
            raise AssertionError(stage + ": controller admitted the packet or was re-activated: " + json.dumps(public(
                {"nonce": row.get("nonce"), "accepted": row.get("accepted"), "witness": witness}), sort_keys=True))
        rejected = row.get("rejected", 0)
        if found is None and rejected <= before:
            if row.get("reason") == "expired" and governing is not None:
                verdict = lapse_evidence(target, rows, index, governing, hold_after_ns, stage)
                if verdict is None:
                    return None
                evidence = {"expected": expected,
                            "published_to_wall_end_ms": (governing["wall_end_ns"] - published) / 1e6}
                if verdict:
                    raise RunnerDelay(stage, dict(evidence, cause="governing_lease_lapsed_before_refusal"))
                raise AssertionError(stage + ": controller locked (expired) before the governing lease could end: "
                                     + json.dumps(public(evidence), sort_keys=True))
            continue
        if published < sent:
            raise AssertionError(stage + ": controller counted a rejection (" + str(row.get("reason"))
                                 + ") before the packet left")
        if rejected != before + 1:
            raise AssertionError(stage + ": counted " + str(rejected - before)
                                 + " rejections, expected exactly one")
        if found is not None:
            continue
        reason = row.get("reason")
        if reason == expected:
            found = index
            continue
        if reason not in timing:
            raise AssertionError(stage + ": rejected for " + str(reason) + ", expected " + expected)
        if reason == "freshness":
            raise RunnerDelay(stage, {"cause": "verifier_freshness_refusal", "expected": expected})
        verdict = lapse_evidence(target, rows, index, governing, hold_after_ns, stage)
        if verdict is None:
            return None
        if verdict:
            raise RunnerDelay(stage, {"cause": "arm_ingress_after_governing_lease_lapsed",
                                      "expected": expected, "reason": reason})
        raise AssertionError(stage + ": arm ingress refused (" + str(reason)
                             + ") before the governing lease could end; expected " + expected)
    if found is None:
        return None
    # This refusal's own latch: a latched guard keeps showing the refusal's
    # reason, because live_unlocked() rewrites it to `expired` only while the
    # guard is still unlocked (permit.hpp:106-114). A later lapse of the lease
    # therefore cannot stand in for a refusal that did not latch.
    reason = rows[found].get("reason")
    latched = next((row for row in rows[found:] if row.get("holding") is True
                    and row.get("reason") == reason), None)
    if target != "arm" or hold_after_ns is None:
        return None if latched is None else (rows[found], latched)
    # The arm must also hold promptly. Its first hold transition after the witness
    # may be an earlier lapse (a late renewal that met a lapsed goal), so the latch
    # is required separately, never on that transition row.
    held = hold_evidence(rows, found, hold_after_ns, stage)
    if held is None or latched is None:
        return None
    if hold_at_lease_end(held, governing):
        raise RunnerDelay(stage, {"cause": "arm_hold_not_separable_from_governing_lease_end",
                                  "expected": expected,
                                  "stop_to_wall_end_ms": (governing["wall_end_ns"] - held["stop_wall_ns"]) / 1e6,
                                  "hold_to_sim_end_ms": (governing["sim_end_ns"]
                                                         - held["cutoff_ms"] * 1_000_000) / 1e6,
                                  "stop_after_send_ms": (held["stop_wall_ns"] - sent) / 1e6})
    return rows[found], held


def refusal_hold(rows, sent_ns, rejected_before, hold_after_ns, stage):
    """(first refusal row, prompt hold row) after an arm packet left; None without both."""
    index = next((index for index, row in enumerate(rows) if row.get("published_wall_ns", 0) >= sent_ns
                  and row.get("rejected", 0) > rejected_before), None)
    if index is None:
        return None
    held = hold_evidence(rows, index, hold_after_ns, stage)
    return None if held is None else (rows[index], held)


def reset_lease_events(target, stage, rows, grant):
    """Judge one controller's telemetry before a negative packet leaves.

    grant: reset()'s record for target. rows: that controller's telemetry since
    reset() saw it unlocked, oldest first, never empty. Nothing was sent, so the
    nonce, both counters and the reason must be unchanged (AssertionError, even for
    a `freshness` refusal), except that the reset lease may have lapsed (`expired`).
    That lapse is judged exactly as on the positive path (require_no_fail_closed):
    a lapse before the lease could end fails, and one at its end raises
    RunnerDelay. Returns None when only time passed, and PENDING while the arm's
    hold evidence is not visible yet.
    """
    row = rows[-1]
    if (row.get("nonce") != grant["nonce"] or row.get("accepted") != grant["accepted"]
            or row.get("rejected") != grant["rejected"]
            or row.get("reason") not in ("accepted", "expired")):
        raise AssertionError(stage + ": controller state changed before the negative packet: " + json.dumps(public(
            {"nonce_matches": row.get("nonce") == grant["nonce"], "accepted": row.get("accepted"),
             "rejected": row.get("rejected"), "reset_accepted": grant["accepted"],
             "reset_rejected": grant["rejected"], "holding": row.get("holding"), "reason": row.get("reason")}),
            sort_keys=True))
    # Counters are unchanged, so the only fail-closed event left is an `expired` lapse.
    return require_no_fail_closed(target, rows, grant["unlocked_wall_ns"], grant["rejected"],
                                  GrantChain(grant["fields"], grant["accepted"] - 1),
                                  grant["unlocked_wall_ns"], stage + " reset lease")


def precondition_miss(target, stage, rows, grant, wall_ns, sim_ns):
    """The live-reset window before a negative packet closed. Inconclusive only if only time passed.

    reset_lease_events() judges the rows first, so a changed controller or a lapse
    before the reset lease could end fails. Returns False while the arm's hold
    evidence is not visible yet; otherwise raises.
    """
    if reset_lease_events(target, stage, rows, grant) is PENDING:
        return False
    row = rows[-1]
    raise RunnerDelay(stage, {"cause": "live_reset_window_missed_before_send", "holding": row.get("holding"),
                              "reason": row.get("reason"),
                              "telemetry_age_ms": (wall_ns - row.get("published_wall_ns", 0)) / 1e6,
                              "wall_to_lease_end_ms": (grant["wall_end_ns"] - wall_ns) / 1e6,
                              "sim_to_lease_end_ms": (grant["sim_end_ns"] - sim_ns) / 1e6})


def motion_after(samples, start_ns, final_positions):
    """Largest arm displacement measured at or after start_ns, or None without evidence.

    samples: [(controller_stamp_ns, positions)] oldest first, stamped on the same
    simulation clock as the controller's update stamps, so receipt delay cannot
    move motion in or out of the window. The bounded history must reach back to
    start_ns; otherwise motion right after it may have been evicted.
    """
    if not samples or samples[0][0] > start_ns:
        return None
    after = [positions for stamp, positions in samples if stamp >= start_ns]
    if not after:
        return None
    return max(abs(a - b) for positions in after + [final_positions]
               for a, b in zip(positions, after[0]))


def refused_motion(stage, drift, hold=None):
    """The robot's drift after a packet that must be refused, on every path.

    Returns drift when it is at most DRIFT_LIMIT. A larger drift is inconclusive
    only with `hold`, the arm replay's prompt hold after the duplicate's refusal
    (arm_hold, which already failed any motion from that refusal on): only the
    admitted first goal can then have moved, before the duplicate arrived. Any
    other drift over the limit, and a drift that is not a finite measurement, fails.
    """
    if not (isinstance(drift, (int, float)) and math.isfinite(drift)):
        raise AssertionError(stage + ": drift after the refused packet was not measured: " + repr(drift))
    if drift <= DRIFT_LIMIT:
        return drift
    if hold is not None:
        raise RunnerDelay(stage, {"cause": "admitted_first_goal_moved_during_duplicate_transit",
                                  "drift": drift, "drift_after_refusal": hold["drift_after_refusal"]})
    raise AssertionError(stage + " moved under invalid permit")


def late_stop_verdict(stage, held, sent_ns, goal):
    """The arm's hold after it refused a deliberately late renewal, against the goal lease.

    held: the first hold transition after the pre-send witness. Conclusive only
    when the hold began after the late renewal left and before the goal lease
    could end on either clock, so the goal's own expiry cannot explain it. A hold
    before the send is inconclusive only as a lapse of the goal lease. (refusal()
    already applies the same lease-end rule; this stays a complete verdict.)
    """
    stop, cutoff_ns = held["stop_wall_ns"], held["cutoff_ms"] * 1_000_000
    lapse = hold_at_lease_end(held, goal)
    evidence = {"stop_to_wall_end_ms": (goal["wall_end_ns"] - stop) / 1e6,
                "hold_to_sim_end_ms": (goal["sim_end_ns"] - cutoff_ns) / 1e6,
                "stop_after_send_ms": (stop - sent_ns) / 1e6}
    if stop >= sent_ns:
        if not lapse:
            return stop
        raise RunnerDelay(stage, dict(evidence, cause="hold_not_separable_from_goal_lease_end"))
    if lapse:
        raise RunnerDelay(stage, dict(evidence, cause="goal_lease_lapsed_before_late_renewal_send"))
    raise AssertionError(stage + ": arm held before the late renewal left and before its goal lease could end: "
                         + json.dumps(public(evidence), sort_keys=True))


@contextlib.contextmanager
def motion_checked(check_motion):
    """After a packet that must be refused has left, an inconclusive outcome still needs no motion.

    check_motion() raises (AssertionError) when the robot moved under the refused
    packet; only then does a RunnerDelay become a failure. Nothing else is caught.
    """
    try:
        yield
    except RunnerDelay:
        check_motion()
        raise


@contextlib.contextmanager
def fail_closed_settled(settle_events):
    """A harness-side RunnerDelay leaves only after the controller's events are judged.

    Used around a positive case's sends and before a negative packet leaves.
    settle_events() classifies what the controller published since the case's
    checkpoint (require_no_fail_closed, or reset_lease_events before a negative
    packet): AssertionError for a failure, RunnerDelay for a timing outcome. When
    there is nothing to judge it returns and the original RunnerDelay is re-raised,
    so a slow approval or send check cannot turn a refusal or premature lock that
    is already visible into a retry. Nothing else is caught.
    """
    try:
        yield
    except RunnerDelay:
        settle_events()
        raise


class AttemptLog:
    """Non-blocking timing record. Writing it can never fail or pass a case."""

    def __init__(self, path, clock=time.monotonic_ns):
        self.path = path
        self.clock = clock
        self.series = {}
        self.attempts = []
        self.complete = False

    def sample(self, name, value_ms):
        if type(value_ms) in (int, float) and math.isfinite(value_ms):
            values = self.series.setdefault(name, [])
            if len(values) < MAX_SERIES_SAMPLES:
                values.append(round(float(value_ms), 3))

    def record(self, case, attempt, outcome, stage=None, evidence=None, wall_ms=None):
        self.attempts.append({"case": case, "attempt": attempt, "outcome": outcome,
                              "stage": stage, "evidence": public(evidence or {}),
                              "wall_ms": public(wall_ms), "at_wall_ns": self.clock()})
        self.write()

    def payload(self):
        cases = {}
        for row in self.attempts:
            entry = cases.setdefault(row["case"], {"attempts": 0, "inconclusive": 0, "outcome": None})
            entry["attempts"] = max(entry["attempts"], row["attempt"])
            entry["inconclusive"] += row["outcome"] == "inconclusive"
            entry["outcome"] = row["outcome"]
        return {"schema_version": 1, "scope": "ci_non_blocking_measurement", "unit": "ms",
                "limit_ms": VERIFIER_WINDOW_NS / 1e6, "max_attempts": MAX_ATTEMPTS,
                "complete": self.complete, "series": self.series,
                "attempts": self.attempts, "cases": cases}

    def write(self):
        try:
            write_checkpoint(self.path, json.dumps(self.payload(), sort_keys=True, allow_nan=False,
                                                   indent=1).encode())
        except (OSError, ValueError, TypeError) as exc:
            print("controller timing record not written: " + str(exc), file=sys.stderr)


def bounded_attempts(case, body, log, attempts=MAX_ATTEMPTS, clock=time.monotonic):
    """Run body() (which begins with its own maintenance reset); retry only RunnerDelay."""
    inconclusive = []
    for attempt in range(1, attempts + 1):
        started = clock()
        try:
            row = body()
        except RunnerDelay as delay:
            inconclusive.append({"attempt": attempt, "stage": delay.stage, **delay.evidence})
            log.record(case, attempt, "inconclusive", delay.stage, delay.evidence,
                       (clock() - started) * 1000)
            print("Inconclusive controller permit attempt, retrying from reset: " + case + " #"
                  + str(attempt) + " " + str(delay), file=sys.stderr, flush=True)
            continue
        except BaseException as exc:
            log.record(case, attempt, "failed", type(exc).__name__, {"error": str(exc)},
                       (clock() - started) * 1000)
            raise
        log.record(case, attempt, "passed", wall_ms=(clock() - started) * 1000)
        return dict(row, attempt=attempt)
    raise AssertionError(case + ": all " + str(attempts) + " attempts were inconclusive under "
                         "runner delay; inconclusive never counts as a pass: "
                         + json.dumps(public(inconclusive), sort_keys=True))
