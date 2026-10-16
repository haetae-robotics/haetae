"""Distinguish measured fail-closed scheduling loss from normal availability.

Shared-runner timing in the board-free H1 bench and H2 signed-permit bench: a
positive-path attempt that missed a deadline or the device lease before any
fault was injected or any stop decision reached the host or relay, and whose
device stopped fail-closed, is timing-inconclusive. A terminal the H2
authorizer logged is never timing-inconclusive even if the relay never read it. Only a
decision made after that deadline that never arrived (and, in H2, that the
authorizer could not finish sending, so it logged no terminal) is not seen and
does not prevent a restart. Inconclusive is never a pass. bounded_attempts()
restarts only such an attempt, from a fresh device emulator and fresh
processes, at most HOST_ATTEMPTS times, and all-inconclusive fails the case.
The separate h2_rejected_fault_stop() path can prove an expected signed-input
rejection and positive-then-OFF independently of late terminal delivery; it never
retries, and reports the missed delivery deadline as a non-blocking measurement.
Every other exception fails at once. The 50 ms and 200 ms limits and the
300 ms observation bound are read here as evidence thresholds only; nothing
here changes them.
"""
import json
import math
import re

HOST_ATTEMPTS = 3
LEASE_MS = 200  # device lease_ms in permit_guard.h and guard.h (pinned by tests); never tuned here
# The 0..300 ms stop delay every passing host attempt must meet in bench_verify.py and
# permit_verify.py (pinned by tests); never tuned here.
OBSERVATION_MS = 300
# OFF rows after ARM that are not failures (the H1 guard logs each STATUS poll while armed).
SETUP_REASONS = frozenset({"armed", "bound"})
STARTUP_REASONS = frozenset({"boot", "hello", "bound", "armed"})
DEADLINE_STOPS = ("lease", "stop", "stale")
TRACEBACK = "Traceback (most recent call last):"


def scheduling_loss(log, code, scenario, events):
    if code != 1 or scenario != "allow" or "device lease locked; explicit new run required" not in log:
        return None
    markers = []
    for line in log.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("event") == "device_lease_expired":
            markers.append(value)
    if len(markers) != 1:
        return None
    marker = markers[0]
    gap = marker.get("host_gap_ms")
    if (marker.get("status") != "LOCKED" or marker.get("automatic_rearm") is not False
            or type(gap) not in (int, float) or not 200 <= gap < float("inf")):
        return None
    for index, stop in enumerate(events):
        prior = [event for event in events[:index] if event.get("on")]
        if not prior or stop.get("on") or stop.get("reason") != "lease":
            continue
        delay = stop["device_ms"] - prior[-1]["renewed_ms"]
        if 200 <= delay <= 300 and not any(event.get("on") for event in events[index + 1:]):
            return stop
    return None


class Inconclusive(Exception):
    """A fail-closed timing loss made this attempt inconclusive. Never a pass.

    Deliberately a plain Exception, not AssertionError, RuntimeError, OSError or
    ValueError: only bounded_attempts() catches it; anywhere else it fails the run.
    """

    def __init__(self, evidence):
        self.evidence = dict(evidence)
        super().__init__(json.dumps(self.evidence, sort_keys=True))


def json_rows(lines, event):
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("event") == event:
            rows.append(value)
    return rows


def renewals(events):
    """Distinct lease renewal stamps (ARM, then each RUN) in device order."""
    stamps = []
    for event in events:
        renewed = event.get("renewed_ms")
        if event.get("reason") in ("armed", "run") and type(renewed) is int and (not stamps or stamps[-1] != renewed):
            stamps.append(renewed)
    return stamps


def device_settled(events):
    """The device left the positive phase: its last row is OFF for a failure or STOP."""
    return bool(events) and not events[-1].get("on") and events[-1].get("reason") not in STARTUP_REASONS


def first_stop_after_on(events, reason=None):
    """First OFF after this attempt's first ON, in append order, never by clock ties.

    A different first stop reason cannot be skipped in favor of a later matching
    row. Callers still reject any ON after the returned stop.
    """
    positive = False
    for event in events:
        if event.get("on") is True:
            positive = True
        elif positive and event.get("on") is False:
            return event if reason is None or event.get("reason") == reason else None
    return None


def fail_closed_stop(events, accepted):
    """Device evidence that the attempt ended fail-closed by one accepted first failure.

    With an ON, the first OFF after the first ON is the failure and any later ON is
    fail-open evidence. Without an ON, it is the first row after 'armed' that is not
    a setup row, so armed -> signature -> stop is a signature failure. 'lease' and
    'unarmed' (a RUN that arrived after the lease ended) count only LEASE_MS or more
    after the governing renewal. 'stop' and 'stale' count only within OBSERVATION_MS
    of it, the bound every passing attempt meets. The emulators write at most one row
    per loop pass, so a lease that ends in the same pass as a STOP can show only as
    the STOP; a later STOP or stale refusal with no lease row before it may have
    ended an output that outlived its lease. Every other reason (signature, replay,
    protocol, partial, ...) is not a timing loss. Returns evidence values or None.
    """
    first_on = next((index for index, event in enumerate(events) if event.get("on")), None)
    if first_on is None:
        armed = next((index for index, event in enumerate(events) if event.get("reason") == "armed"), None)
        if armed is None:
            return None  # never armed: not a loss inside the bounded renewal loop
        renewal = events[armed]
        failure = next((event for event in events[armed + 1:] if event.get("reason") not in SETUP_REASONS), None)
    else:
        off = next((index for index in range(first_on + 1, len(events)) if not events[index].get("on")), None)
        if off is None or any(event.get("on") for event in events[off + 1:]):
            return None  # never OFF, or ON again after OFF: a real failure
        renewal, failure = events[off - 1], events[off]
    if failure is None or failure.get("on") or failure.get("reason") not in accepted:
        return None
    stopped, renewed = failure.get("device_ms"), renewal.get("renewed_ms")
    if type(stopped) is not int or type(renewed) is not int:
        return None
    delay = stopped - renewed
    if failure["reason"] in ("lease", "unarmed"):
        if delay < LEASE_MS:
            return None  # the device lease cannot end early; host timing does not explain it
    elif not 0 <= delay <= OBSERVATION_MS:
        return None  # no lease row first: the output may have outlived its lease, never a timing loss
    stamps = renewals(events)
    return {"positive_control": first_on is not None, "stop_reason": failure["reason"],
            "renewal_reason": renewal.get("reason"), "last_run_to_off_ms": delay,
            "max_renewal_gap_ms": max((b - a for a, b in zip(stamps, stamps[1:])), default=None),
            "emulator_lateness_ms": delay - LEASE_MS if failure["reason"] == "lease" else None}


# H2: the relay's last log line is printed by haetae-permit for errors raised in
# tools/permit_link.py and tools/permit_ipc.py (strings pinned by tests).
H2_ERROR_PREFIX = "permit 오류: "
H2_LEASE = "controller locked; explicit operator restart required"
H2_REPLY_PREFIX = "invalid H2 controller response: "
# STATUS still answered ARMED/ON, then the lease ended before the RUN verified.
H2_LOCKED_REPLY = re.compile(re.escape(H2_REPLY_PREFIX) + r"H2 ERR [0-9a-f]{32}(?: (?:0|[1-9][0-9]*)){5} "
                             r"[0-9a-f]{32} LOCKED (?:unarmed|lease)")
# Raised only after a monotonic deadline passed, never by a content check.
H2_DEADLINES = frozenset({"timed out", "permit IPC frame deadline expired", "authorizer permit arrived late",
                          "late controller acknowledgment", "H2 USB response timeout", "H2 USB write timeout"})
H2_DECISIONS = ("Authorizer fault injected:", "Authorizer terminal:")


def elapsed_ms(start_ns, end_ns):
    if type(start_ns) is not int or type(end_ns) is not int:
        return None
    return round((end_ns - start_ns) / 1e6, 3)


def authorizer_stage(authorizer_log, relay_stage):
    """The stage of the authorizer's last trace that read the relay's request, or None.

    Both processes stamp time.monotonic_ns(); the matching stage is the first read at or after
    the relay started sending. None when the authorizer printed no trace for that request.
    """
    started = relay_stage.get("request_send_started_ns")
    traces = json_rows(authorizer_log.splitlines(), "permit_ipc_failure")
    stages = traces[-1].get("stages") if traces else None
    if type(started) is not int or not isinstance(stages, list):
        return None
    reads = [stage for stage in stages if isinstance(stage, dict)
             and type(stage.get("request_first_byte_ns")) is int and stage["request_first_byte_ns"] >= started]
    return min(reads, key=lambda stage: stage["request_first_byte_ns"]) if reads else None


def authorizer_timing(authorizer_log, relay_stage):
    """Read delay and gate cycle of the authorizer stage that read the relay's last request."""
    timing = {"authorizer_read_delay_ms": None, "gate_cycle_ms": None}
    read = authorizer_stage(authorizer_log, relay_stage)
    if read is not None:
        timing["authorizer_read_delay_ms"] = elapsed_ms(relay_stage.get("request_sent_ns"),
                                                        read["request_first_byte_ns"])
        timing["gate_cycle_ms"] = elapsed_ms(read.get("gate_started_ns"), read.get("gate_completed_ns"))
    return timing


def h2_timing_loss(relay_log, code, authorizer_log, events):
    """Evidence that a positive-path H2 attempt ended only by a missed deadline or lease, fail-closed.

    Read final logs only (both processes exited). Requires relay exit 1 whose last line is a lease
    or deadline message; no authorizer fault or terminal line, so no fault was injected and no stop
    decision was logged, even one the relay never read; no Python traceback in either log; exactly
    one relay permit_ipc_failure trace, so the loss happened inside the bounded renewal loop; a
    completed authorizer send for a reply the relay received but the controller never acknowledged
    (below), so no stop decision reached the relay unlogged; and fail_closed_stop() device evidence.
    A terminal the authorizer could not finish sending and the relay did not receive before its own
    deadline is not seen and does not prevent a restart. Returns None for anything else, which stays
    an ordinary failure. Never a pass.

    The authorizer prints a terminal only after send_line returns, and send_line checks its deadline
    again after sendall, so a terminal can reach the relay with no terminal line in the authorizer's
    log. So when the relay's last stage has response_received_ns but no controller_ack_ns, the
    authorizer stage that read that request (authorizer_stage) must have response_sent_ns: a
    completed send means any terminal it carried was logged.

    haetae-permit prints each RuntimeError, OSError or ValueError, which includes every timing
    error, as one "permit 오류:" line, and stop_pair's SIGKILL prints nothing. A traceback is an
    uncaught crash (KeyError, TypeError, ...), a defect even when it coincides with a slow cycle.
    """
    lines = [line for line in relay_log.splitlines() if line.strip()]
    if code != 1 or not lines or not lines[-1].startswith(H2_ERROR_PREFIX):
        return None
    error = lines[-1][len(H2_ERROR_PREFIX):]
    if error == H2_LEASE:
        kind, accepted = "lease", ("lease",)
    elif H2_LOCKED_REPLY.fullmatch(error):
        kind, accepted = "lease", ("lease", "unarmed")
    elif error in H2_DEADLINES:
        kind, accepted = "deadline", DEADLINE_STOPS
    else:
        return None  # every content error, including H2 ERR signature/replay/protocol/partial/stale
    if any(marker in authorizer_log for marker in H2_DECISIONS):
        return None
    if TRACEBACK in authorizer_log or TRACEBACK in relay_log:
        return None
    traces = json_rows(lines, "permit_ipc_failure")
    stages = traces[0].get("stages") if len(traces) == 1 else None
    if (not isinstance(stages, list) or not stages or not all(
            isinstance(stage, dict) and stage.get("op") in ("BIND", "ARM", "RUN") for stage in stages)):
        return None
    if "response_received_ns" in stages[-1] and "controller_ack_ns" not in stages[-1]:
        # The relay received the reply and the controller never acknowledged that request: a permit
        # it could not complete, or a terminal (a stop decision) followed by a failed STOP. A test
        # that runs the real relay pins this key; if it drifted, such a terminal would be restarted.
        read = authorizer_stage(authorizer_log, stages[-1])
        if read is None or type(read.get("response_sent_ns")) is not int:
            return None  # no completed send, so a terminal it carried may be missing from the log
    stop = fail_closed_stop(events, accepted)
    if stop is None:
        return None
    evidence = dict(stop, kind=kind, error=error, relay_last_stage=stages[-1])
    evidence.update(authorizer_timing(authorizer_log, stages[-1]))
    return evidence


def h2_rejected_fault_stop(relay_log, code, authorizer_log, events, scenario):
    """Conclusive rejection + OFF, with late terminal delivery measured separately.

    Only the two signed-input rejection scenarios qualify. This is neither a retry nor
    proof of a timely terminal: the relay's unchanged 50 ms wait still failed. Require
    the exact Rust rejection, zero command, a completed unsigned terminal (no MAC),
    matched request stages and positive device evidence within the existing stop bound.
    Old diagnostics without the exact rejection/zero-command fields cannot qualify.
    """
    expected = {"replay": "replay", "invalid-signature": "signature"}.get(scenario)
    lines = [line for line in relay_log.splitlines() if line.strip()]
    auth_lines = [line for line in authorizer_log.splitlines() if line.strip()]
    if (expected is None or code != 1 or not lines
            or lines[-1] not in (H2_ERROR_PREFIX + "timed out",
                                H2_ERROR_PREFIX + "permit IPC frame deadline expired")
            or TRACEBACK in relay_log or TRACEBACK in authorizer_log
            or any(line.startswith(H2_ERROR_PREFIX) for line in auth_lines)
            or [line for line in auth_lines if line.startswith(H2_DECISIONS)] != [
                "Authorizer fault injected: " + scenario, "Authorizer terminal: gate_denied"]):
        return None
    relay_traces = json_rows(lines, "permit_ipc_failure")
    denials = json_rows(auth_lines, "permit_gate_denial_diagnostic")
    if len(relay_traces) != 1 or len(denials) != 1:
        return None
    relay_stages, stages, cycles = (relay_traces[0].get("stages"), denials[0].get("stages"),
                                    denials[0].get("cycles"))
    if not all(isinstance(value, list) and value and all(isinstance(row, dict) for row in value)
               for value in (relay_stages, stages, cycles)):
        return None
    relay, auth, cycle = relay_stages[-1], stages[-1], cycles[-1]
    responses = cycle.get("responses")
    if (relay.get("op") != "RUN" or "response_received_ns" in relay or "controller_ack_ns" in relay
            or "sign_completed_ns" in auth or cycle.get("result") != "denied"
            or not isinstance(responses, list) or len(responses) != 2
            or not all(isinstance(row, dict) for row in responses)):
        return None
    world, motion = responses
    if (world.get("phase") != "world" or world.get("outcome") != "world_updated" or world.get("stop") is not None
            or motion.get("phase") != "motion" or motion.get("outcome") != "rejected"
            or motion.get("rejection_reason") != expected or motion.get("command_zero") is not True
            or motion.get("stop") != "denied" or motion.get("status_stop") != "denied"
            or motion.get("vla_armed") is not False or motion.get("active_present") is not False
            or motion.get("mode") != "normal" or motion.get("recorder_ok") is not True
            or motion.get("state_ok") is not True or motion.get("arm_cancelling") is not False):
        return None
    # Correlate the denial with the relay's final unanswered RUN, not an earlier cycle.
    clocks = [relay.get("started_ns"), relay.get("request_send_started_ns"), relay.get("request_sent_ns"),
              auth.get("request_first_byte_ns"), auth.get("request_received_ns"), auth.get("gate_started_ns"),
              world.get("request_started_ns"), world.get("response_received_ns"),
              motion.get("request_started_ns"), motion.get("response_received_ns"),
              auth.get("gate_completed_ns"), auth.get("response_send_started_ns"), auth.get("response_sent_ns")]
    if not all(type(value) is int and value >= 0 for value in clocks) or clocks != sorted(clocks):
        return None
    stop = fail_closed_stop(events, DEADLINE_STOPS)
    if stop is None or stop["positive_control"] is not True or not 0 <= stop["last_run_to_off_ms"] <= OBSERVATION_MS:
        return None
    return {"path": "verified-rejection", "rejection_reason": expected, "blocking": False,
            "terminal_delivery": "deadline-missed", "error": lines[-1][len(H2_ERROR_PREFIX):],
            "gate_cycle_ms": elapsed_ms(auth["gate_started_ns"], auth["gate_completed_ns"]),
            "request_to_terminal_sent_ms": elapsed_ms(relay["started_ns"], auth["response_sent_ns"])}


# H1: bench_host.run() ends with an uncaught exception; the log's last line is the
# traceback's "module.Class: message" (strings pinned by tests).
H1_LEASE = "device lease locked; explicit new run required"
H1_REPLY_PREFIX = "unexpected USB response: "
H1_LOCKED_REPLY = re.compile(re.escape(H1_REPLY_PREFIX) + r"H1 ERR [0-9a-f]{16} (?:0|[1-9][0-9]*) "
                             r"(?:0|[1-9][0-9]*) LOCKED (?:unarmed|lease)")
H1_STALE = re.compile(r"stale actuation response: elapsed [0-9]+\.[0-9] ms >= 50 ms")
H1_USB_DEADLINES = ("USB response timeout", "USB write timeout")
H1_DECISIONS = ("시험: ", "차단: ", "시험 종료: ")  # fault injected, gate stop, allow completed
# bench_host.run sends this STOP only after a stop decision (a gate stop or the allow completion)
# and prints the 차단/시험 종료 line only after it returns. Its finally's link.stop() swallows
# LinkError and OSError, so this source line in a traceback is a STOP that failed after a decision.
H1_STOP_CALL = 'link.exchange("STOP")'
H1_ALLOWED = "허용: LED ON (테스트 명령이 통과 중)"
H1_DENIED = "차단: LED OFF, 재시작은 새 run 명령으로만 가능합니다."


def h1_pause_gate_stop(host_log, code, ready, events, stopped):
    """A paused host resumed into its clean gate-denial path, after the device lease ended.

    Called with final host output and device rows, after the harness observed `stopped`
    before SIGCONT. Exact host messages alone are insufficient: require positive RUN,
    a subsequent non-renewing STATUS, the first OFF by lease, and only STOPs afterward.
    STATUS keeps reason 'run'; its incremented sequence and unchanged renewal identify it.
    This is conclusive interruption evidence, never a timing-inconclusive restart.
    """
    if (code != 0 or [line for line in host_log.splitlines() if line.strip()] != [H1_ALLOWED, H1_DENIED]
            or not isinstance(ready, dict) or "fault" not in ready or ready["fault"] is not None):
        return None
    if not all(isinstance(event, dict) for event in events):
        return None
    first_on = next((index for index, event in enumerate(events) if event.get("on") is True), None)
    if first_on is None:
        return None
    off = next((index for index in range(first_on + 1, len(events)) if events[index].get("on") is not True), None)
    if off is None or off < first_on + 2 or events[off] != stopped:
        return None
    run, status, lease = events[off - 2:off + 1]
    tail = events[off + 1:]
    relevant = [run, status, lease] + tail
    if not tail or not all(type(event.get(key)) is int for event in relevant
                           for key in ("device_ms", "renewed_ms", "sequence")):
        return None
    renewed, sequence = run["renewed_ms"], status["sequence"]
    if (run.get("on") is not True or status.get("on") is not True
            or run.get("reason") != "run" or status.get("reason") != "run"
            or sequence != run["sequence"] + 1 or lease["sequence"] != sequence
            or lease.get("on") is not False or lease.get("reason") != "lease"
            or any(event["renewed_ms"] != renewed for event in relevant)
            or not 0 <= renewed <= run["device_ms"] <= status["device_ms"] <= lease["device_ms"]
            or not LEASE_MS <= lease["device_ms"] - renewed <= OBSERVATION_MS):
        return None
    previous = lease
    for event in tail:
        # The denial sends STOP, then finally sends one best-effort STOP. The emulator
        # may observe both in one loop and log only the second sequence.
        if (event.get("on") is not False or event.get("reason") != "stop"
                or not previous["sequence"] < event["sequence"] <= sequence + 2
                or event["device_ms"] < previous["device_ms"]):
            return None
        previous = event
    return {"resume_path": "gate-stop", "last_run_to_off_ms": lease["device_ms"] - renewed}


def h1_error_kind(line):
    exception, separator, message = line.partition(": ")
    if not separator:
        return None
    if exception == "bench_link.LinkError":
        if message == H1_LEASE:
            return "lease", ("lease",)
        if H1_LOCKED_REPLY.fullmatch(message):
            return "lease", ("lease", "unarmed")
        if message in H1_USB_DEADLINES:
            return "deadline", DEADLINE_STOPS
    if exception == "bridge.StaleActuation" and H1_STALE.fullmatch(message):
        return "deadline", DEADLINE_STOPS
    if exception == "bridge.BridgeFailure" and message == "gate response timeout":
        return "deadline", DEADLINE_STOPS
    return None


def h1_timing_loss(host_log, code, ready, events):
    """Evidence that a positive-path H1 attempt ended only by a missed deadline or lease, fail-closed.

    `ready` is the parsed ready file, or None when the host never wrote one. Requires host exit 1
    with a traceback whose last line is a timing exception; ready fault null, no fault, gate-stop
    or allow-completion line and no failed STOP after a stop decision (H1_STOP_CALL); and
    fail_closed_stop() device evidence. Returns None otherwise.
    """
    lines = [line for line in host_log.splitlines() if line.strip()]
    if code != 1 or not lines or TRACEBACK not in lines:
        return None
    found = h1_error_kind(lines[-1])
    if found is None:
        return None
    if ready is not None and not (isinstance(ready, dict) and ready.get("fault") is None):
        return None
    if any(line.startswith(H1_DECISIONS) or line.strip() == H1_STOP_CALL for line in lines):
        return None
    stop = fail_closed_stop(events, found[1])
    if stop is None:
        return None
    return dict(stop, kind=found[0], error=lines[-1])


def read_events(path):
    """Complete JSON rows of a native device audit log; a partial last row is skipped."""
    rows = []
    for row in path.read_text().splitlines(keepends=True):
        if row.endswith("\n"):
            try:
                value = json.loads(row)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def cadence(events, protocol):
    """Raw renewal timing samples in ms from one native device log. Never a pass/fail input.

    renewal_gap_ms: between consecutive RUN renewals, each restarting the 200 ms lease.
    arm_to_first_run_ms: from the ARM renewal to the first RUN renewal (the ARM lease window).
    status_to_run_verified_ms: STATUS challenge to RUN verified, the 50 ms path. H2 rows carry
    the challenge issue time in renewed_ms; H1 logs a STATUS row just before each RUN.
    """
    runs, verified, armed, previous = [], [], None, None
    for event in events:
        renewed, logged = event.get("renewed_ms"), event.get("device_ms")
        if type(renewed) is int and type(logged) is int:
            if event.get("reason") == "armed" and armed is None:
                armed = renewed
            elif event.get("reason") == "run" and event.get("on") and (not runs or runs[-1] != renewed):
                runs.append(renewed)
                if protocol == "H2":
                    verified.append(logged - renewed)
                elif (previous is not None and type(previous.get("device_ms")) is int
                      and type(event.get("sequence")) is int
                      and previous.get("sequence") == event["sequence"] - 1):
                    verified.append(renewed - previous["device_ms"])
        previous = event
    return {"renewal_gap_ms": [b - a for a, b in zip(runs, runs[1:])],
            "arm_to_first_run_ms": [runs[0] - armed] if runs and armed is not None else [],
            "status_to_run_verified_ms": verified}


def distribution(values):
    """Nearest-rank p50/p99/max of finite samples; {"n": 0} when there are none."""
    ordered = sorted(value for value in values if type(value) in (int, float) and math.isfinite(value))
    if not ordered:
        return {"n": 0}
    def rank(quantile):
        return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]
    return {"n": len(ordered), "p50": rank(.5), "p99": rank(.99), "max": ordered[-1]}


class TimingRecord:
    """Non-blocking per-attempt measurement and inconclusive evidence for one bench run.

    Nothing here is a pass/fail input. report.json carries it, written in a finally
    block, so runner timing and the 50 ms path stay visible even when every attempt
    passes and when a case fails.
    """

    def __init__(self, protocol):
        self.protocol = protocol
        self.attempts = []
        self.inconclusive = []
        self.samples = {"renewal_gap_ms": [], "arm_to_first_run_ms": [], "status_to_run_verified_ms": []}

    def measure(self, case, attempt, log, outcome, device_log):
        row = {"case": case, "attempt": attempt, "log": log, "outcome": outcome}
        try:
            samples = cadence(read_events(device_log), self.protocol)
            row["renewal_gap_ms"] = distribution(samples["renewal_gap_ms"])
            row["arm_to_first_run_ms"] = (samples["arm_to_first_run_ms"] or [None])[0]
            row["status_to_run_verified_ms"] = distribution(samples["status_to_run_verified_ms"])
            for key, values in samples.items():
                self.samples[key].extend(values)
        except Exception as exc:  # measurement only: it never changes an attempt's outcome
            row["measurement_error"] = (type(exc).__name__ + ": " + str(exc))[:300]
        self.attempts.append(row)

    def payload(self):
        by_case = {}
        for record in self.inconclusive:
            by_case[record["case"]] = by_case.get(record["case"], 0) + 1
        return {"blocking": False, "protocol": self.protocol, "max_attempts": HOST_ATTEMPTS,
                "inconclusive_total": len(self.inconclusive), "inconclusive_by_case": by_case,
                "inconclusive": list(self.inconclusive),
                "pooled": {key: distribution(values) for key, values in self.samples.items()},
                "attempts": list(self.attempts)}


def warning(protocol, case, attempt, evidence, restart):
    """One GitHub Actions warning line; printed by the bench step only (tests capture stdout).

    Only an attempt before the last says it restarts; after the last one the case fails.
    """
    positive = "seen" if evidence.get("positive_control") else "not reached"
    next_step = (f"restarting with a {restart}" if attempt < HOST_ATTEMPTS
                 else "no attempts left; the case fails (inconclusive never counts as a pass)")
    text = (f"{protocol} {case} attempt {attempt}/{HOST_ATTEMPTS} timing-inconclusive, not a pass: "
            f"{evidence.get('kind')} loss ({evidence.get('error')}); positive ON {positive}; first device OFF: "
            f"{evidence.get('stop_reason')} {evidence.get('last_run_to_off_ms')} ms after the last renewal, "
            f"no later ON; {next_step}")
    return "::warning::" + text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def bounded_attempts(case, attempt, timing, device_log, restart):
    """Run attempt(label) at most HOST_ATTEMPTS times; only Inconclusive restarts it.

    Each restart is a fresh start with its own logs (case, case-retry1, case-retry2).
    Any other exception fails the case at once, and HOST_ATTEMPTS inconclusive attempts
    fail it. Every attempt is measured (non-blocking) and each inconclusive attempt
    prints one warning line.
    """
    records = []
    for number in range(1, HOST_ATTEMPTS + 1):
        label = case if number == 1 else case + "-retry" + str(number - 1)
        outcome = "failed"
        try:
            result = dict(attempt(label), case=case, attempt=number, log=label)
            outcome = "passed"
            return result
        except Inconclusive as exc:
            outcome = "inconclusive"
            record = dict(exc.evidence, case=case, attempt=number, log=label)
            records.append(record)
            timing.inconclusive.append(record)
            print(warning(timing.protocol, case, number, exc.evidence, restart), flush=True)
        finally:
            timing.measure(case, number, label, outcome, device_log(label))
    raise AssertionError(case + ": all " + str(HOST_ATTEMPTS) + " fresh attempts were timing-inconclusive;"
                         " inconclusive never counts as a pass: " + json.dumps(records, sort_keys=True))
