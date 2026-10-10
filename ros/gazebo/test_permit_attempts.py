"""Runner delay may make a controller-permit probe attempt inconclusive, never passing."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest

from permit_attempts import (MAX_ATTEMPTS, PENDING, VERIFIER_WINDOW_NS, AttemptLog, GrantChain, RunnerDelay,
                             bounded_attempts, check_timely, fail_closed_settled, goal_counted, hold_evidence,
                             late_stop_verdict, motion_after, motion_checked, negative_timing, permit_fields,
                             precondition_miss, refusal, refusal_hold, refused_motion, require_fresh_witness,
                             require_live, require_no_fail_closed, require_refused_inside_lease,
                             require_refused_inside_stale_window, require_stale_window, reset_lease_events)
from public_report import (CONTROLLER_UPDATE_NS, DRIFT_LIMIT, HOLD_UPDATES, LAPSE_ALLOWANCE_NS, MAX_LEASE_NS,
                           NEGATIVE_REASONS, PERMIT_NEGATIVES, arm_hold_prompt, negative_permit_rejected,
                           refused_inside_lease, stale_permit_refused_in_window)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "ros/haetae_gate"))
from bridge import BridgeFailure, ExpiredActuation, StaleActuation, require_fresh_actuation  # noqa: E402

MS = 1_000_000
NONCE = "b" * 32


def nested(*path, tree=None):
    """The function at path (outer to inner) in controller_probes.py, or in its parsed tree.
    Nested names such as check_motion repeat across cases, so a flat name map would keep
    only the last one."""
    node = tree or ast.parse((HERE / "controller_probes.py").read_text())
    for name in path:
        node = next(child for child in ast.walk(node) if isinstance(child, ast.FunctionDef) and child.name == name)
    return node


def flat(node):
    # ast.unparse spaces lambdas differently across Python versions.
    return "".join(ast.unparse(node).split())


def grant(kind="arm-goal", sim=1_000 * MS, wall=5_000 * MS, life=180 * MS):
    return {"kind": kind, "seq": 2, "sim_ns": sim, "wall_ns": wall,
            "sim_end_ns": sim + life, "wall_end_ns": wall + life}


def row(published_ms, rejected=4, accepted=3, reason="accepted", holding=False, **extra):
    return {"published_wall_ns": published_ms * MS, "rejected": rejected, "accepted": accepted,
            "reason": reason, "holding": holding, "nonce": NONCE, **extra}


def arm(published_ms, stamp_ms, rejected=4, accepted=3, reason="accepted", holding=False,
        stop_ms=3_000, cutoff_ms=900):
    """Arm telemetry: stamp_ms is the controller's last update (simulation clock); the
    default hold stamps belong to an earlier case, before every hold_after used here."""
    return row(published_ms, rejected, accepted, reason, holding, stamp_ms=stamp_ms,
               stop_wall_ns=stop_ms * MS, cutoff_ms=cutoff_ms)


def held(published_ms, stamp_ms, stop_ms, cutoff_ms, rejected=5, accepted=3, reason="accepted"):
    return arm(published_ms, stamp_ms, rejected, accepted, reason, True, stop_ms, cutoff_ms)


def goal_row(published_ms, stamp_ms, accepted=4, **extra):
    """Arm telemetry once the late-renewal harness's goal (digest d...d) was admitted (accepted 4)."""
    return dict(arm(published_ms, stamp_ms, accepted=accepted, **extra), active_digest="d" * 64)


class ReachedRecovery(Exception):
    """The actual negative() judged its refusal a pass and went on to the recovery packet."""


class ReachedRenewals(Exception):
    """The actual arm_positive() saw its goal admitted and went on to the renewals."""


class LogStub:
    def __init__(self):
        self.records = []

    def record(self, case, attempt, outcome, stage=None, evidence=None, wall_ms=None):
        self.records.append((case, attempt, outcome, stage))


class ScriptedBase:
    """A scripted base controller and clock for the actual positive-case code.

    Each permit the harness sends is counted `transit` 10 ms ticks later as its
    scripted outcome says ("admit", "admit twice" for a controller that counts one
    admission twice, "drop" or a refusal reason). Admitted commands move the base
    1 cm per tick until their lease lapses as `expired`. The reset lease (wall end
    5180 ms) governs first; telemetry is published every tick.
    """

    def __init__(self, outcomes, transit=2):
        self.clock = {"ns": 5_010 * MS}
        self.outcomes, self.transit = list(outcomes), transit
        self.state = {"accepted": 3, "rejected": 4, "reason": "accepted", "holding": False}
        self.rows = [(0, row(5_005, **self.state))]
        self.queue, self.lease_end, self.moving, self.cm = [], 5_180 * MS, False, 0

    def send(self, fields):
        self.queue.append([self.transit, self.outcomes.pop(0), fields])

    def publish(self):
        self.rows.append((0, row(self.clock["ns"] // MS, **self.state)))

    def tick(self):
        self.clock["ns"] += 10 * MS
        for item in self.queue:
            item[0] -= 1
        while self.queue and self.queue[0][0] <= 0:
            _, outcome, fields = self.queue.pop(0)
            if outcome in ("admit", "admit twice") and self.state["holding"]:
                outcome = "locked"
            if outcome in ("admit", "admit twice"):
                counted = 2 if outcome == "admit twice" else 1
                self.state.update(accepted=self.state["accepted"] + counted, reason="accepted")
                self.lease_end, self.moving = fields["wall_end_ns"], True
            elif outcome != "drop":
                self.state.update(rejected=self.state["rejected"] + 1, reason=outcome, holding=True)
        if not self.state["holding"] and self.clock["ns"] >= self.lease_end:
            self.state.update(reason="expired", holding=True)
        if self.moving and not self.state["holding"]:
            self.cm += 1
        self.publish()

    def wait_for(self, predicate, timeout, processes, description, action=None):
        for _ in range(int(timeout * 100)):
            self.tick()
            if predicate():
                return
            if action is not None:
                action()
        raise TimeoutError(description)


class BoundedAttemptsTest(unittest.TestCase):
    def run_body(self, outcomes):
        calls, log = [], LogStub()
        def body():
            calls.append(1)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return calls, log, (lambda: bounded_attempts("arm_positive", body, log))

    @staticmethod
    @contextlib.contextmanager
    def retry_log():
        """Capture bounded_attempts()'s retry lines, so the Gazebo job log shows only real probe retries."""
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            yield captured

    def test_inconclusive_attempts_retry_from_scratch_and_are_recorded(self):
        calls, log, run = self.run_body([RunnerDelay("arm renewal", {"cause": "late"}), {"ok": True, "moved_rad": .09}])
        with self.retry_log() as printed:
            self.assertEqual(run(), {"ok": True, "moved_rad": .09, "attempt": 2})
        self.assertEqual(len(calls), 2)
        self.assertEqual([entry[2] for entry in log.records], ["inconclusive", "passed"])
        self.assertEqual(log.records[0][3], "arm renewal")
        self.assertEqual(printed.getvalue(), "Inconclusive controller permit attempt, retrying from reset: "
                                             'arm_positive #1 arm renewal: {"cause": "late"}\n')

    def test_all_inconclusive_is_a_failure_not_a_pass(self):
        calls, log, run = self.run_body([RunnerDelay("late")] * MAX_ATTEMPTS)
        with self.retry_log() as printed, self.assertRaisesRegex(AssertionError, "inconclusive never counts as a pass"):
            run()
        self.assertEqual(printed.getvalue().splitlines()[-1],
                         "Inconclusive controller permit attempt, no attempts left: arm_positive #3 late: {}")
        self.assertEqual(len(calls), MAX_ATTEMPTS)
        # The record ends with the case's failure, never a pass.
        self.assertEqual([entry[1:] for entry in log.records],
                         [(attempt, "inconclusive", "late") for attempt in range(1, MAX_ATTEMPTS + 1)]
                         + [(MAX_ATTEMPTS, "failed", "AssertionError")])

    def test_real_failures_are_never_retried(self):
        for failure in (AssertionError("moved under invalid permit"), TimeoutError("no motion"),
                        RuntimeError("gazebo exited"), StaleActuation("missing expiry"),
                        BridgeFailure("gate process exited"), ValueError("token"), KeyboardInterrupt()):
            calls, log, run = self.run_body([failure, {"ok": True}])
            with self.assertRaises(type(failure)):
                run()
            self.assertEqual(len(calls), 1, repr(failure))
            self.assertEqual([entry[2] for entry in log.records], ["failed"])

    def test_runner_delay_is_not_a_verdict_class_and_budget_fits_reset_ids(self):
        self.assertFalse(issubclass(RunnerDelay, (AssertionError, TimeoutError, RuntimeError)))
        self.assertEqual(MAX_ATTEMPTS, 3)
        # 2 positives + 12 negatives + 1 late renewal, one reset per attempt;
        # controller_reset.py accepts identifiers 1..100.
        self.assertLessEqual(15 * MAX_ATTEMPTS, 100)


class VerifierBoundTest(unittest.TestCase):
    def test_harness_mirrors_unchanged_verifier_controller_and_signer_limits(self):
        self.assertEqual(VERIFIER_WINDOW_NS, 50_000_000)
        header = (REPO / "ros/haetae_arm_guard/include/haetae_arm_guard/permit.hpp").read_text()
        self.assertIn("s - next.sim >= 50000000 || w - next.wall >= 50000000", header)
        self.assertIn("next.sim_end - next.sim > 200000000", header)
        # current_goal_stamp: the cutoff and 50 ms simulation window goal_window_evidence relies on.
        self.assertIn("if (stamp <= 0 || stamp < cutoff || now < 0) {return false;}", header)
        self.assertIn("now - stamp < 50000000", header)
        source = (REPO / "ros/haetae_gate/controller_permits.py").read_text()
        self.assertRegex(source, r"\nMAX_LEASE_NS = 200_000_000\n")
        self.assertRegex(source, r"\nSIM_ORDERING_BACKDATE_NS = 10_000_000\n")
        self.assertEqual(MAX_LEASE_NS, 200_000_000)
        # One controller update at the pinned 100 Hz; the lapse allowance is the
        # simulation backdate plus one update, never a tuned tolerance.
        self.assertRegex((HERE / "controllers.yaml").read_text(), r"\n    update_rate: 100\n")
        self.assertEqual(CONTROLLER_UPDATE_NS, 10_000_000)
        self.assertEqual(LAPSE_ALLOWANCE_NS, 10_000_000 + CONTROLLER_UPDATE_NS)
        self.assertEqual(HOLD_UPDATES, 3)

    def test_arm_hold_stamps_are_stored_at_the_first_update_after_the_lock(self):
        controller = (REPO / "ros/haetae_arm_guard/src/controller.cpp").read_text()
        self.assertRegex(controller, r"holding_\.store\(true\);\s+if \(!was_holding_\) \{\s+cutoff_ms_\.store\(stamp\);\s+"
                                     r"stop_wall_ns_\.store\(wall_ns\(\)\);")
        self.assertIn('",\\"stamp_ms\\":" + std::to_string(updated_ms_.load())', controller)


class PreSendTest(unittest.TestCase):
    def test_permit_leaves_only_inside_the_verifier_predicate(self):
        fields = permit_fields("v1:arm-lease:" + "a" * 32 + ":4:990000000:5000000000:1170000000:5180000000:"
                               + "c" * 64 + ":" + "d" * 128)
        self.assertEqual((fields["kind"], fields["seq"], fields["wall_end_ns"]), ("arm-lease", 4, 5_180_000_000))
        self.assertAlmostEqual(check_timely("renewal", fields, 5_049_999_999, 1_039_999_999), 49.999999)
        for wall, sim in ((5_050_000_000, 1_000_000_000), (5_000_000_000, 1_040_000_000),
                          (5_180_000_000, 1_000_000_000), (5_000_000_000, 1_170_000_000)):
            with self.assertRaises(RunnerDelay) as delay:
                check_timely("renewal", fields, wall, sim)
            self.assertEqual(delay.exception.evidence["cause"], "permit_aged_before_send")
        for malformed in ("", "v1:arm-lease", "v2" + ":x" * 9, None):
            with self.assertRaises(ValueError):
                permit_fields(malformed)

    def test_a_short_lease_is_withheld_once_its_own_end_passed_under_the_age_bound(self):
        # A 30 ms lease 35 ms old on one clock: younger than 50 ms, but past its own end.
        short = grant("arm-lease", sim=1_000 * MS, wall=5_000 * MS, life=30 * MS)
        self.assertAlmostEqual(check_timely("x", short, 5_029 * MS, 1_029 * MS), 29.0)
        for wall, sim in ((5_035 * MS, 1_010 * MS), (5_010 * MS, 1_035 * MS)):
            with self.assertRaises(RunnerDelay) as delay:
                check_timely("x", short, wall, sim)
            self.assertEqual(delay.exception.evidence["age_ms"], 35.0)

    def test_probe_and_report_share_the_hold_and_stale_age_edges(self):
        # A hold exactly HOLD_UPDATES updates after the refusal row is prompt for both.
        refused = arm(5_130, 1_120, rejected=5, reason="binding")
        limit = 1_120 + HOLD_UPDATES * CONTROLLER_UPDATE_NS // MS          # 1150 ms
        for cutoff, prompt in ((limit, True), (limit + 1, False)):
            hold = held(5_190, cutoff, stop_ms=5_131, cutoff_ms=cutoff, reason="binding")
            try:
                probe = hold_evidence([refused, hold], 0, 5_090 * MS, "x") is hold
            except AssertionError:
                probe = False
            report = arm_hold_prompt({"controller_hold": {"refusal_stamp_ms": 1_120, "hold_cutoff_ms": cutoff,
                                                          "drift_after_refusal": 0.}})
            self.assertEqual((probe, report), (prompt, prompt), cutoff)
        # A deliberately stale permit exactly 50 ms old at send is refusable only by the age bound.
        stale = grant("arm-goal", sim=940 * MS, wall=4_940 * MS, life=200 * MS)
        for age, refusable in ((VERIFIER_WINDOW_NS, True), (VERIFIER_WINDOW_NS - 1, False)):
            sent = stale["wall_ns"] + age
            try:
                require_stale_window("x", stale, sent, stale["sim_ns"] + VERIFIER_WINDOW_NS)
                probe = True
            except AssertionError:
                probe = False
            report = stale_permit_refused_in_window({
                "stale_permit": {key: stale[key] for key in ("sim_ns", "wall_ns", "sim_end_ns", "wall_end_ns")},
                "negative_admission": {"sent_wall_ns": sent, "rejection_published_wall_ns": sent + 10 * MS}})
            self.assertEqual((probe, report), (refusable, refusable), age)

    def test_age_uses_the_signed_backdated_origin_the_verifier_compares(self):
        from controller_permits import IDLE, PermitSigner, SIM_ORDERING_BACKDATE_NS
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "controller.key"
            key.write_text("11" * 32)
            signer = PermitSigner(key, sim_backdate_ns=SIM_ORDERING_BACKDATE_NS)
            signer.observe("arm", {"nonce": "a" * 32})
            token = signer.sign("arm", "lease", IDLE, 5_000 * MS, 180 * MS, 7_000 * MS)
        fields = permit_fields(token)
        # 30 ms of harness sim time is already 40 ms at the verifier (10 ms backdate).
        self.assertAlmostEqual(check_timely("x", fields, 7_030 * MS, 5_030 * MS), 40.0)
        with self.assertRaises(RunnerDelay):
            check_timely("x", fields, 7_001 * MS, 5_040 * MS)

    def test_governing_grant_and_witness_must_be_live_at_send(self):
        live = grant()
        require_live("negative", live, live["wall_end_ns"] - 1, live["sim_end_ns"] - 1)
        for wall, sim in ((live["wall_end_ns"], 0), (0, live["sim_end_ns"])):
            with self.assertRaises(RunnerDelay):
                require_live("negative", live, wall, sim)
        require_fresh_witness("negative", 100, 100 + VERIFIER_WINDOW_NS - 1)
        for sent in (100 + VERIFIER_WINDOW_NS, 99):
            with self.assertRaises(RunnerDelay):
                require_fresh_witness("negative", 100, sent)

    def test_probe_and_report_share_one_pre_send_witness_window(self):
        # A witness the probe sends against is one the report accepts, and the reverse,
        # at both edges of the same VERIFIER_WINDOW_NS definition.
        witness = {"nonce_before": NONCE, "nonce_after": NONCE, "holding_before": False,
                   "reason_before": "accepted", "rejection_reason": "binding",
                   "accepted_before": 3, "accepted_after": 3, "rejected_before": 4, "rejected_after": 5,
                   "sent_wall_ns": 5_100 * MS, "rejection_published_wall_ns": 5_120 * MS,
                   "lease_wall_end_ns": 5_180 * MS}
        for age in (0, VERIFIER_WINDOW_NS - 1, VERIFIER_WINDOW_NS, -1):
            witness["before_published_wall_ns"] = witness["sent_wall_ns"] - age
            try:
                require_fresh_witness("x", witness["before_published_wall_ns"], witness["sent_wall_ns"])
                sent = True
            except RunnerDelay:
                sent = False
            self.assertEqual(sent, 0 <= age < VERIFIER_WINDOW_NS, age)
            self.assertEqual(negative_permit_rejected(witness, "unsigned"), sent, age)

    def test_stale_permit_is_refusable_only_by_the_age_bound(self):
        stale = grant("arm-lease", sim=940 * MS, wall=4_940 * MS, life=200 * MS)
        require_stale_window("late renewal", stale, 5_000 * MS, 1_000 * MS)
        # Under 50 ms old on either clock is a construction error, never runner delay.
        for wall, sim in ((4_989 * MS, 1_000 * MS), (5_000 * MS, 989 * MS)):
            with self.assertRaisesRegex(AssertionError, "under 50 ms old"):
                require_stale_window("late renewal", stale, wall, sim)
        # Its own ends already passed: the end clause could refuse it, so do not send it.
        for wall, sim in ((5_140 * MS, 1_000 * MS), (5_000 * MS, 1_140 * MS)):
            with self.assertRaises(RunnerDelay) as delay:
                require_stale_window("late renewal", stale, wall, sim)
            self.assertEqual(delay.exception.evidence["cause"], "stale_permit_ends_passed_before_send")
        require_refused_inside_stale_window("x", row(5_119), stale)
        with self.assertRaises(RunnerDelay) as delay:
            require_refused_inside_stale_window("x", row(5_120), stale)
        self.assertEqual(delay.exception.evidence["cause"], "refusal_not_separable_from_stale_permit_end")


class PositiveClassificationTest(unittest.TestCase):
    """A lock on a timely permit is inconclusive only with evidence of a signed bound."""

    def setUp(self):
        self.reset = grant("arm-reset", sim=1_000 * MS, wall=5_000 * MS)    # ends 1180 / 5180 ms
        self.goal = grant("arm-goal", sim=1_030 * MS, wall=5_040 * MS)      # ends 1210 / 5220 ms
        self.chain = GrantChain(self.reset, 2)  # accepted 3 once the reset was admitted
        self.chain.sent(self.goal)

    def classify(self, rows, target="arm", chain=None):
        return require_no_fail_closed(target, rows, 100 * MS, 4, chain or self.chain, 4_000 * MS, target)

    def test_no_event_and_events_before_the_checkpoint_are_ignored(self):
        self.assertIsNone(self.classify([arm(5_010, 1_010)]))
        self.assertIsNone(self.classify([arm(90, 90, rejected=9, reason="signature")]))

    def test_freshness_refusal_is_inconclusive(self):
        for target in ("base", "arm"):
            with self.assertRaises(RunnerDelay) as delay:
                self.classify([arm(5_050, 1_050, rejected=5, reason="freshness", holding=True)], target)
            self.assertEqual(delay.exception.evidence["cause"], "verifier_freshness_refusal")

    def test_other_reasons_and_multiple_refusals_fail(self):
        for target in ("base", "arm"):
            for reason in ("binding", "sequence", "signature", "token bounds", "stop", "startup"):
                with self.assertRaisesRegex(AssertionError, "timely permit rejected for " + reason):
                    self.classify([arm(5_050, 1_050, rejected=5, reason=reason)], target)
        with self.assertRaisesRegex(AssertionError, "rejected for rejected"):
            self.classify([row(5_200, rejected=5, reason="rejected")], "base")
        # Two refusals in one row hide the first reason: never inconclusive.
        for reason in ("locked", "freshness"):
            with self.assertRaises(AssertionError):
                self.classify([row(5_200, rejected=6, reason=reason)], "base")
        # The first event decides; a later timing reason cannot excuse an earlier failure.
        with self.assertRaises(AssertionError):
            self.classify([row(5_050, rejected=5, reason="binding"), row(5_060, rejected=6, reason="freshness")], "base")

    def test_base_lock_before_the_governing_lease_could_end_fails(self):
        base_reset = grant("base-reset", sim=1_000 * MS, wall=5_000 * MS)
        command = grant("base-command", sim=1_060 * MS, wall=5_070 * MS)    # ends 5250 ms
        chain = GrantChain(base_reset, 2)
        chain.sent(command)
        boundary = (base_reset["wall_end_ns"] - LAPSE_ALLOWANCE_NS) // MS   # 5160 ms
        for reason, rejected in (("locked", 5), ("expired", 4)):
            # The reset governed (accepted 3): a lock visible 60 ms before it could end fails.
            with self.assertRaisesRegex(AssertionError, "before any signed bound could close"):
                self.classify([row(5_100, rejected=rejected, reason=reason, holding=True)], "base", chain)
            with self.assertRaises(RunnerDelay) as delay:
                self.classify([row(boundary, rejected=rejected, reason=reason, holding=True)], "base", chain)
            self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
            # Once the command was admitted (accepted 4) its own, later end governs.
            with self.assertRaises(AssertionError):
                self.classify([row(boundary, rejected=rejected, accepted=4, reason=reason, holding=True)], "base", chain)
            with self.assertRaises(RunnerDelay):
                self.classify([row(5_230, rejected=rejected, accepted=4, reason=reason, holding=True)], "base", chain)

    def test_arm_lapse_needs_the_controllers_own_hold_at_a_lease_end(self):
        event = arm(5_100, 1_100, reason="expired", holding=True)
        # Hold stamps not visible yet: undecided, not inconclusive.
        self.assertEqual(self.classify([event]), PENDING)
        early = held(5_105, 1_105, stop_ms=5_101, cutoff_ms=1_101, rejected=4, reason="expired")
        with self.assertRaisesRegex(AssertionError, "before any signed bound could close"):
            self.classify([event, early])
        # The wall end is exact (same CLOCK_MONOTONIC); the simulation end allows one update.
        for stop_ms, cutoff_ms, stamp_ms in ((5_180, 1_100, 1_100), (5_101, 1_170, 1_170)):
            lapse = arm(5_190, stamp_ms, reason="expired", holding=True)
            hold = held(5_195, stamp_ms, stop_ms=stop_ms, cutoff_ms=cutoff_ms, rejected=4, reason="expired")
            with self.assertRaises(RunnerDelay) as delay:
                self.classify([lapse, hold])
            self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
        just_before = held(5_195, 1_169, stop_ms=5_179, cutoff_ms=1_169, rejected=4, reason="expired")
        with self.assertRaises(AssertionError):
            self.classify([arm(5_190, 1_169, reason="expired", holding=True), just_before])

    def test_arm_that_keeps_updating_without_holding_fails(self):
        event = arm(5_100, 1_100, rejected=5, reason="locked")
        self.assertEqual(self.classify([event, arm(5_120, 1_130, rejected=5, reason="locked")]), PENDING)
        with self.assertRaisesRegex(AssertionError, "kept updating"):
            self.classify([event, arm(5_150, 1_131, rejected=5, reason="locked")])
        late = held(5_190, 1_140, stop_ms=5_185, cutoff_ms=1_131, reason="locked")
        with self.assertRaisesRegex(AssertionError, "more than 3 updates"):
            self.classify([event, late])

    def test_arm_rejected_goal_is_inconclusive_only_past_its_own_window(self):
        # accept() admitted the goal (accepted 4) and the action handshake refused it.
        event = arm(5_090, 1_075, rejected=5, accepted=4, reason="rejected")
        window = (self.goal["sim_ns"] + VERIFIER_WINDOW_NS - CONTROLLER_UPDATE_NS) // MS   # 1070 ms
        with self.assertRaises(RunnerDelay) as delay:
            self.classify([event, held(5_095, 1_075, 5_091, window, accepted=4, reason="rejected")])
        self.assertEqual(delay.exception.evidence["cause"], "goal_window_closed_during_action_handshake")
        early = arm(5_060, 1_050, rejected=5, accepted=4, reason="rejected")
        with self.assertRaises(AssertionError):
            self.classify([early, held(5_065, 1_050, 5_061, window - 1, accepted=4, reason="rejected")])
        # Refused at the ingress (not counted): only a lapse of the reset lease can explain it.
        ingress = arm(5_090, 1_085, rejected=5, accepted=3, reason="rejected")
        with self.assertRaises(AssertionError):
            self.classify([ingress, held(5_095, 1_085, 5_091, 1_085, reason="rejected")])
        lapsed = arm(5_190, 1_172, rejected=5, accepted=3, reason="rejected")
        with self.assertRaises(RunnerDelay):
            self.classify([lapsed, held(5_195, 1_172, 5_181, 1_172, reason="rejected")])

    def test_admission_counts_must_match_the_permits_sent(self):
        chain = GrantChain(grant("base-reset"), 2)
        self.assertEqual(chain.admitted(2, "x"), (-1, None))
        self.assertEqual(chain.admitted(3, "x")[0], 0)
        # One positive permit in flight: the next leaves only once the controller counted this one.
        self.assertEqual([chain.in_flight(count) for count in (2, 3, None)], [True, False, True])
        chain.sent(grant("base-command"))
        self.assertEqual([chain.in_flight(count) for count in (3, 4)], [True, False])
        chain.grants.pop()
        for count in (1, 4, None, "3"):
            with self.assertRaisesRegex(AssertionError, "does not match"):
                chain.admitted(count, "x")
        # Reset (nothing admitted yet): a reset is never refused for `locked`.
        with self.assertRaises(AssertionError):
            self.classify([row(5_200, rejected=5, accepted=2, reason="locked", holding=True)], "base", chain)


class NegativeRefusalTest(unittest.TestCase):
    """An admission is checked before any timing classification."""

    def setUp(self):
        self.witness = {"sent_wall_ns": 5_100 * MS, "nonce_before": NONCE, "accepted_before": 3, "rejected_before": 4}
        self.reset = grant("arm-reset", sim=1_000 * MS, wall=5_000 * MS)    # ends 1180 / 5180 ms

    def judge(self, rows, expected="signature", timing=("freshness",), target="base", hold_after=None):
        return refusal(target, rows, self.witness, expected, timing, self.reset, target + " case", hold_after)

    def test_expected_held_refusal_is_returned_with_its_hold(self):
        refused = row(5_120, rejected=5, reason="signature", holding=True)
        self.assertEqual(self.judge([row(5_090), refused]), (refused, refused))
        self.assertIsNone(self.judge([row(5_090), row(5_099, rejected=5, reason="signature", holding=True)]))
        not_held = row(5_120, rejected=5, reason="signature")
        self.assertIsNone(self.judge([not_held]))
        later = row(5_140, rejected=5, reason="signature", holding=True)
        self.assertEqual(self.judge([not_held, later]), (not_held, later))

    def test_hold_must_be_the_refusals_own_latch(self):
        # A counted refusal that does not latch leaves the guard unlocked
        # under the reset lease until live_unlocked() lapses it as `expired`. That later
        # hold is not this refusal's latch, so the case stays undecided and times out.
        self.assertIsNone(self.judge([row(5_120, rejected=5, reason="signature"),
                                      row(5_190, rejected=5, reason="expired", holding=True)]))
        # The recovery judgement (no hold_after) uses the same rule on the arm.
        self.assertIsNone(self.judge([arm(5_130, 1_120, rejected=5, reason="rejected"),
                                      arm(5_190, 1_180, rejected=5, reason="expired", holding=True)],
                                     "rejected", (), "arm"))

    def test_arm_prompt_hold_must_also_be_the_refusals_own_latch(self):
        # An arm refusal that does not latch leaves the guard unlocked until
        # the reset lease lapses as `expired`. That lapse hold lands within three updates of
        # the refusal row, so it is prompt, but it is not this refusal's latch: never a pass.
        for case in ("unsigned", "altered", "target", "delay", "signature", "replay"):
            expected = NEGATIVE_REASONS[case]
            rows = [arm(5_150, 1_150, rejected=5, reason=expected),
                    held(5_181, 1_172, stop_ms=5_172, cutoff_ms=1_172, reason="expired")]
            self.assertIsNone(self.judge(rows, expected, negative_timing("arm", case), "arm", 5_090 * MS), case)
        rows = [arm(5_130, 1_140, rejected=5, reason="binding"),
                held(5_175, 1_160, stop_ms=5_171, cutoff_ms=1_160, reason="expired")]
        self.assertIsNone(self.judge(rows, "binding", (), "arm", 5_090 * MS))
        # A latched refusal keeps its reason; the same prompt hold then counts.
        latched = held(5_175, 1_160, stop_ms=5_141, cutoff_ms=1_141, reason="binding")
        self.assertEqual(self.judge([rows[0], latched], "binding", (), "arm", 5_090 * MS), (rows[0], latched))

    def test_arm_hold_at_the_governing_lease_end_is_inconclusive(self):
        # A latched refusal whose hold stamps reach the end of the lease the arm relied on
        # (self.reset ends at 1180 ms sim / 5180 ms wall) could be that lease's own lapse.
        refused = arm(5_165, 1_160, rejected=5, reason="binding")
        for stop_ms, cutoff_ms in ((5_180, 1_160), (5_175, 1_170)):
            hold = held(5_185, cutoff_ms, stop_ms=stop_ms, cutoff_ms=cutoff_ms, reason="binding")
            with self.assertRaises(RunnerDelay) as delay:
                self.judge([refused, hold], "binding", (), "arm", 5_090 * MS)
            self.assertEqual(delay.exception.evidence["cause"], "arm_hold_not_separable_from_governing_lease_end")
        # 1 ms before the wall end and before the simulation bound, the hold is the refusal's.
        hold = held(5_185, 1_169, stop_ms=5_179, cutoff_ms=1_169, reason="binding")
        self.assertEqual(self.judge([refused, hold], "binding", (), "arm", 5_090 * MS), (refused, hold))
        # The latch is required first: an unlatched refusal never becomes inconclusive this way.
        lapse = held(5_185, 1_170, stop_ms=5_180, cutoff_ms=1_170, reason="expired")
        self.assertIsNone(self.judge([refused, lapse], "binding", (), "arm", 5_090 * MS))

    def test_late_renewal_meeting_a_lapsed_goal_is_inconclusive_not_a_timeout(self):
        # The goal lease (ends 1180 ms sim / 5180 ms wall) lapsed in the controller just
        # before the late renewal reached it; the renewal is then refused for freshness and
        # latches. The earlier `expired` hold transition is not required to carry the latch.
        goal = grant("arm-goal", sim=1_000 * MS, wall=5_000 * MS)
        witness = dict(self.witness, sent_wall_ns=5_170 * MS)
        rows = [held(5_169, 1_180, stop_ms=5_168, cutoff_ms=1_180, rejected=4, reason="expired"),
                held(5_190, 1_185, stop_ms=5_168, cutoff_ms=1_180, reason="freshness")]
        with self.assertRaises(RunnerDelay) as delay:
            refusal("arm", rows, witness, "freshness", negative_timing("arm", "renewal_delay"), goal,
                    "arm renewal_delay", 5_090 * MS)
        self.assertEqual(delay.exception.evidence["cause"], "arm_hold_not_separable_from_governing_lease_end")
        # late_renewal()'s own witness names its pre-send row, so the published lapse is judged first.
        with self.assertRaises(RunnerDelay) as delay:
            refusal("arm", rows, dict(witness, before_published_wall_ns=5_090 * MS), "freshness",
                    negative_timing("arm", "renewal_delay"), goal, "arm renewal_delay", 5_090 * MS)
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed_before_refusal")
        # A hold before the send that no lease end explains is still late_stop_verdict's failure.
        early = [held(5_120, 1_110, stop_ms=5_110, cutoff_ms=1_110, rejected=4, reason="accepted"),
                 held(5_190, 1_130, stop_ms=5_110, cutoff_ms=1_110, reason="freshness")]
        refused, hold = refusal("arm", early, witness, "freshness", (), goal, "arm renewal_delay", 5_090 * MS)
        with self.assertRaisesRegex(AssertionError, "before the late renewal left"):
            late_stop_verdict("arm renewal_delay", hold, witness["sent_wall_ns"], goal)

    def test_admission_of_the_refused_packet_fails_before_timing(self):
        # The arm verifier admits a stale goal (accepted +1) and the
        # handshake then refuses it as `rejected`. That is a failure, never runner delay.
        admitted = arm(5_150, 1_150, rejected=5, accepted=4, reason="rejected")
        with self.assertRaisesRegex(AssertionError, "admitted the packet"):
            self.judge([admitted], "freshness", negative_timing("arm", "delay"), "arm", 5_090 * MS)
        # An admission with no refusal yet fails at once too.
        with self.assertRaisesRegex(AssertionError, "admitted the packet"):
            self.judge([row(5_110, accepted=4)])
        with self.assertRaisesRegex(AssertionError, "admitted the packet"):
            self.judge([dict(row(5_110), nonce="c" * 32)])
        # Rows before the packet left are not its outcome.
        self.assertIsNone(self.judge([row(5_090, accepted=4)]))

    def test_timing_reasons_need_evidence_and_others_fail(self):
        with self.assertRaises(RunnerDelay):
            self.judge([row(5_120, rejected=5, reason="freshness", holding=True)])
        for reason in ("binding", "locked", "expired", "rejected", "sequence"):
            with self.assertRaisesRegex(AssertionError, "expected signature"):
                self.judge([row(5_120, rejected=5, reason=reason, holding=True)])
        with self.assertRaisesRegex(AssertionError, "exactly one"):
            self.judge([row(5_120, rejected=6, reason="signature", holding=True)])
        with self.assertRaisesRegex(AssertionError, "exactly one"):
            self.judge([row(5_120, rejected=5, reason="signature"), row(5_140, rejected=6, reason="signature")])
        # Deliberately stale cases still require the verifier's own freshness reason.
        for case in ("delay", "renewal_delay"):
            with self.assertRaises(AssertionError):
                self.judge([row(5_120, rejected=5, reason="locked", holding=True)], "freshness",
                           negative_timing("arm", case), "arm", 5_090 * MS)

    def test_other_reasons_fail_as_themselves_where_lapse_evidence_would_be_inconclusive(self):
        # A reason outside `timing` must fail as that reason, never reach the lapse
        # judgement, even where that judgement would place it at the governing lease's end.
        boundary = (self.reset["wall_end_ns"] - LAPSE_ALLOWANCE_NS) // MS    # 5160 ms
        for reason in ("binding", "locked", "rejected", "sequence"):
            with self.assertRaisesRegex(AssertionError, "rejected for " + reason + ", expected signature"):
                self.judge([row(boundary + 10, rejected=5, reason=reason, holding=True)])
        # An arm `delay` refused for `binding`, with its latched hold stamped at the reset lease's end.
        rows = [arm(5_185, 1_172, rejected=5, reason="binding"),
                held(5_190, 1_175, stop_ms=5_181, cutoff_ms=1_172, reason="binding")]
        with self.assertRaisesRegex(AssertionError, "rejected for binding, expected freshness"):
            self.judge(rows, "freshness", negative_timing("arm", "delay"), "arm", 5_090 * MS)

    def test_arm_ingress_refusal_is_inconclusive_only_after_the_governing_lease_lapsed(self):
        timing = negative_timing("arm", "unsigned")
        early = arm(5_130, 1_120, rejected=5, reason="rejected")
        self.assertIsNone(self.judge([early], "binding", timing, "arm", 5_090 * MS))
        with self.assertRaisesRegex(AssertionError, "before the governing lease could end"):
            self.judge([early, held(5_135, 1_120, 5_121, 1_120, reason="rejected")], "binding", timing, "arm", 5_090 * MS)
        late = arm(5_185, 1_172, rejected=5, reason="rejected")
        with self.assertRaises(RunnerDelay) as delay:
            self.judge([late, held(5_190, 1_172, 5_181, 1_172, reason="rejected")], "binding", timing, "arm", 5_090 * MS)
        self.assertEqual(delay.exception.evidence["cause"], "arm_ingress_after_governing_lease_lapsed")

    def test_arm_refusal_needs_a_prompt_hold(self):
        refused = arm(5_130, 1_120, rejected=5, reason="binding")
        self.assertIsNone(self.judge([refused], "binding", (), "arm", 5_090 * MS))
        prompt = held(5_140, 1_130, 5_131, 1_130, reason="binding")
        self.assertEqual(self.judge([refused, prompt], "binding", (), "arm", 5_090 * MS), (refused, prompt))
        with self.assertRaisesRegex(AssertionError, "more than 3 updates"):
            self.judge([refused, held(5_190, 1_180, 5_181, 1_151, reason="binding")], "binding", (), "arm", 5_090 * MS)
        with self.assertRaisesRegex(AssertionError, "kept updating"):
            self.judge([refused, arm(5_190, 1_151, rejected=5, reason="binding")], "binding", (), "arm", 5_090 * MS)
        # A hold stamped before the pre-send witness is someone else's.
        self.assertIsNone(self.judge([refused, held(5_140, 1_130, 5_000, 1_000, reason="binding")],
                                     "binding", (), "arm", 5_090 * MS))
        self.assertEqual(refusal_hold([row(5_090), refused, prompt], 5_100 * MS, 4, 5_090 * MS, "x"), (refused, prompt))
        self.assertIsNone(refusal_hold([row(5_090), refused], 5_100 * MS, 4, 5_090 * MS, "x"))

    def test_recovery_at_a_latched_arm_needs_no_new_hold(self):
        latched = arm(5_130, 1_120, rejected=5, reason="rejected", holding=True)
        self.assertEqual(self.judge([latched], "rejected", (), "arm"), (latched, latched))

    def test_negative_timing_follows_verifier_check_order(self):
        # Only `signature` can be overtaken by `freshness`; arm goals meet the action ingress
        # first. Heartbeats have no action precheck: a late renewal has no timing alternative.
        special = {("base", "signature"): ("freshness",), ("arm", "signature"): ("freshness", "rejected"),
                   ("arm", "renewal_delay"): ()}
        for target, case in PERMIT_NEGATIVES:
            self.assertEqual(negative_timing(target, case),
                             special.get((target, case), ("rejected",) if target == "arm" else ()), (target, case))

    def test_lapse_published_before_the_refusal_is_judged_not_skipped(self):
        # accept() lapses an ended lease and then overwrites `expired` with its own refusal
        # reason, so the refusal row alone looks latched. Here an `expired` row 75 ms
        # before the reset lease's wall end, then the refusal, for every base case.
        witness = dict(self.witness, before_published_wall_ns=5_090 * MS)    # sent 5100 ms
        boundary = (self.reset["wall_end_ns"] - LAPSE_ALLOWANCE_NS) // MS    # 5160 ms
        for target, case in PERMIT_NEGATIVES:
            if target != "base":
                continue
            expected = NEGATIVE_REASONS[case]
            # Also between the pre-send witness and the send (5095 ms), and 1 ms before the allowance.
            for lapse_ms in (5_095, 5_105, boundary - 1):
                rows = [row(5_090), row(lapse_ms, reason="expired", holding=True),
                        row(lapse_ms + 20, rejected=5, reason=expected, holding=True)]
                with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before the governing lease could end"):
                    refusal("base", rows, witness, expected, negative_timing("base", case), self.reset, case)
            for lapse_ms in (boundary, boundary + 10):
                rows = [row(5_090), row(lapse_ms, reason="expired", holding=True),
                        row(lapse_ms + 20, rejected=5, reason=expected, holding=True)]
                with self.assertRaises(RunnerDelay) as delay:
                    refusal("base", rows, witness, expected, negative_timing("base", case), self.reset, case)
                self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed_before_refusal")
        # The arm's lapse is judged by its own hold stamps (pending until they are visible).
        lapse = arm(5_150, 1_150, reason="expired")
        refused = arm(5_170, 1_160, rejected=5, reason="rejected")
        self.assertIsNone(refusal("arm", [lapse, refused], witness, "binding", negative_timing("arm", "unsigned"),
                                  self.reset, "arm unsigned", 5_090 * MS))
        early = held(5_155, 1_150, stop_ms=5_141, cutoff_ms=1_141, rejected=4, reason="expired")
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before the governing lease could end"):
            refusal("arm", [lapse, early, refused], witness, "binding", (), self.reset, "arm unsigned", 5_090 * MS)
        at_end = held(5_185, 1_175, stop_ms=5_180, cutoff_ms=1_175, rejected=4, reason="expired")
        with self.assertRaises(RunnerDelay):
            refusal("arm", [lapse, at_end, refused], witness, "binding", (), self.reset, "arm unsigned", 5_090 * MS)

    def test_only_time_may_change_between_the_pre_send_witness_and_the_send(self):
        witness = dict(self.witness, before_published_wall_ns=5_090 * MS)    # sent 5100 ms
        refused = row(5_120, rejected=5, reason="binding", holding=True)
        self.assertEqual(refusal("base", [row(5_090), row(5_095), refused], witness, "binding", (), self.reset, "x"),
                         (refused, refused))
        # A rejection counted before the packet left is never its refusal.
        with self.assertRaisesRegex(AssertionError, r"counted a rejection \(binding\) before the packet left"):
            refusal("base", [row(5_090), row(5_095, rejected=5, reason="binding", holding=True)], witness,
                    "binding", (), self.reset, "x")
        for changed in (row(5_095, accepted=4), dict(row(5_095), nonce="c" * 32)):
            with self.assertRaisesRegex(AssertionError, "admitted the packet or was re-activated"):
                refusal("base", [row(5_090), changed, refused], witness, "binding", (), self.reset, "x")
        # Rows before the witness row are not judged; nor, without a witness row (the
        # recovery packet), rows before the send.
        lapsed = row(5_080, reason="expired", holding=True)
        self.assertEqual(refusal("base", [lapsed, refused], witness, "binding", (), self.reset, "x"),
                         (refused, refused))
        self.assertEqual(refusal("base", [dict(lapsed, published_wall_ns=5_095 * MS), refused], self.witness,
                                 "binding", (), self.reset, "x"), (refused, refused))

    def test_base_refusal_near_the_lease_end_is_inconclusive_in_probe_and_report(self):
        # Boundary case: a lapse in the last 20 ms is never published (the base
        # publishes every 20 ms), and the refusal then overwrites `expired`. refusal() cannot
        # see it; the refusal's own publication is too close to the lease end to count.
        witness = dict(self.witness, before_published_wall_ns=5_090 * MS, sent_wall_ns=5_150 * MS)
        refused = row(5_172, rejected=5, reason="binding", holding=True)
        self.assertEqual(refusal("base", [row(5_160), refused], witness, "binding", (), self.reset, "x"),
                         (refused, refused))
        end = self.reset["wall_end_ns"]
        for target, published, conclusive in (("base", end - LAPSE_ALLOWANCE_NS - 1, True),
                                              ("base", end - LAPSE_ALLOWANCE_NS, False),
                                              ("base", refused["published_wall_ns"], False),
                                              ("arm", end - 1, True), ("arm", end, False)):
            try:
                require_refused_inside_lease(target, "x", {"published_wall_ns": published}, end)
                probe = True
            except RunnerDelay as delay:
                self.assertEqual(delay.evidence["cause"], "rejection_not_separable_from_lease_end")
                probe = False
            report = refused_inside_lease(target, {"rejection_published_wall_ns": published, "lease_wall_end_ns": end})
            self.assertEqual((probe, report), (conclusive, conclusive), (target, published))

    @staticmethod
    def reset_record(lease, unlocked_ms):
        """reset()'s record for one controller: counters after the admitted reset."""
        return {"nonce": NONCE, "accepted": 3, "rejected": 4, "fields": lease, "unlocked_wall_ns": unlocked_ms * MS,
                "wall_end_ns": lease["wall_end_ns"], "sim_end_ns": lease["sim_end_ns"]}

    def test_precondition_miss_is_inconclusive_only_when_nothing_but_time_changed(self):
        lease = grant("base-reset", sim=1_000 * MS, wall=1_010 * MS)     # wall end 1190 ms
        live = self.reset_record(lease, 1_020)
        def miss(rows):
            return precondition_miss("base", "p", rows, live, 1_210 * MS, 1_200 * MS)
        # Stale telemetry still showing the live reset: only time passed.
        with self.assertRaises(RunnerDelay) as delay:
            miss([row(1_040), row(1_100)])
        self.assertEqual(delay.exception.evidence["cause"], "live_reset_window_missed_before_send")
        # The same judgement without a closed window (a harness delay) has nothing to report.
        self.assertIsNone(reset_lease_events("base", "p", [row(1_040), row(1_100)], live))
        # A lapse first published within LAPSE_ALLOWANCE_NS of the signed wall end.
        boundary = (lease["wall_end_ns"] - LAPSE_ALLOWANCE_NS) // MS     # 1170 ms
        with self.assertRaises(RunnerDelay) as delay:
            miss([row(1_100), row(boundary, reason="expired", holding=True), row(1_190, reason="expired", holding=True)])
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
        # The reset lease lapsed before it could end. The positive path fails
        # this lock, and so does the precondition, although the latest row alone looks late.
        for early in (1_090, boundary - 1):
            with self.assertRaisesRegex(AssertionError, "before any signed bound could close"):
                miss([row(early, reason="expired", holding=True), row(1_200, reason="expired", holding=True)])
        # Anything but time changing fails at once, before any lapse is judged; a counted
        # refusal before the packet left is never runner delay, even for `freshness`.
        for changed in (row(1_100, rejected=5, reason="freshness", holding=True), row(1_100, rejected=5),
                        row(1_100, accepted=4), dict(row(1_100), nonce="c" * 32),
                        row(1_100, reason="binding"), row(1_100, reason="startup")):
            with self.assertRaisesRegex(AssertionError, "controller state changed"):
                miss([row(1_040), changed])

    def test_precondition_arm_lapse_needs_its_hold_at_the_reset_lease_end(self):
        live = self.reset_record(self.reset, 5_010)                      # ends 1180 / 5180 ms
        def miss(rows):
            return precondition_miss("arm", "p", rows, live, 5_210 * MS, 1_200 * MS)
        lapse = arm(5_190, 1_181, reason="expired")
        # Hold stamps not visible yet: keep waiting, never a verdict.
        self.assertIs(miss([lapse]), False)
        self.assertIs(reset_lease_events("arm", "p", [lapse], live), PENDING)
        with self.assertRaises(RunnerDelay) as delay:
            miss([lapse, held(5_200, 1_181, stop_ms=5_181, cutoff_ms=1_181, rejected=4, reason="expired")])
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
        early = arm(5_100, 1_090, reason="expired")
        with self.assertRaisesRegex(AssertionError, "before any signed bound could close"):
            miss([early, held(5_110, 1_095, stop_ms=5_091, cutoff_ms=1_091, rejected=4, reason="expired")])


class MotionTest(unittest.TestCase):
    """Motion after the controller's refusal is bounded on every path."""

    def test_motion_is_measured_on_the_controllers_clock_from_the_window_start(self):
        samples = [(1_000 * MS, [.100, 0, 0, 0]), (1_010 * MS, [.104, 0, 0, 0]),
                   (1_020 * MS, [.130, 0, 0, 0]), (1_030 * MS, [.104, 0, 0, 0])]
        # Motion away and back after the start still counts.
        self.assertAlmostEqual(motion_after(samples, 1_010 * MS, [.104, 0, 0, 0]), .026)
        self.assertAlmostEqual(motion_after(samples, 1_030 * MS, [.105, 0, 0, 0]), .001)
        # History that no longer reaches back to the start may have evicted motion.
        self.assertIsNone(motion_after(samples[1:], 1_005 * MS, [.104, 0, 0, 0]))
        self.assertIsNone(motion_after(samples, 1_031 * MS, [.104, 0, 0, 0]))
        self.assertIsNone(motion_after([], 1_000 * MS, [.104, 0, 0, 0]))

    def test_late_renewal_hold_must_follow_send_and_precede_the_goal_lease_end(self):
        goal = grant("arm-goal", sim=1_000 * MS, wall=5_000 * MS)    # ends 1180 / 5180 ms
        sent = 5_100 * MS
        hold = held(5_120, 1_110, stop_ms=5_110, cutoff_ms=1_110)
        self.assertEqual(late_stop_verdict("s", hold, sent, goal), 5_110 * MS)
        for stop_ms, cutoff_ms, cause in ((5_180, 1_110, "hold_not_separable_from_goal_lease_end"),
                                          (5_110, 1_170, "hold_not_separable_from_goal_lease_end"),
                                          (5_090, 1_170, "goal_lease_lapsed_before_late_renewal_send")):
            with self.assertRaises(RunnerDelay) as delay:
                late_stop_verdict("s", held(5_195, cutoff_ms, stop_ms, cutoff_ms), sent, goal)
            self.assertEqual(delay.exception.evidence["cause"], cause)
        # A hold before the send that no lease end explains is a fault, not runner delay.
        with self.assertRaisesRegex(AssertionError, "before the late renewal left"):
            late_stop_verdict("s", held(5_095, 1_090, 5_090, 1_090), sent, goal)

    def test_inconclusive_outcome_after_a_valid_arm_goal_still_needs_it_counted(self):
        calls = []
        def counted():
            calls.append("counted")
        with goal_counted(counted):
            pass
        self.assertEqual(calls, [])
        delay = RunnerDelay("signed arm physically moves", {"cause": "governing_lease_lapsed"})
        with self.assertRaises(RunnerDelay) as raised:
            with goal_counted(counted):
                raise delay
        self.assertIs(raised.exception, delay)
        self.assertEqual(calls, ["counted"])
        def never():
            raise AssertionError("signed arm physically moves: the controller never counted a valid arm goal")
        with self.assertRaisesRegex(AssertionError, "never counted a valid arm goal"):
            with goal_counted(never):
                raise delay
        # Failures and timeouts are never intercepted.
        for failure in (AssertionError("timely permit rejected for binding"), TimeoutError("no admission")):
            with self.assertRaises(type(failure)) as raised:
                with goal_counted(never):
                    raise failure
            self.assertIs(raised.exception, failure)

    def test_inconclusive_outcome_after_send_still_requires_no_motion(self):
        calls = []
        def still():
            calls.append("checked")
        with motion_checked(still):
            pass
        self.assertEqual(calls, [])
        with self.assertRaises(RunnerDelay):
            with motion_checked(still):
                raise RunnerDelay("arm signature", {"cause": "verifier_freshness_refusal"})
        self.assertEqual(calls, ["checked"])
        def moved():
            raise AssertionError("arm signature moved under invalid permit")
        with self.assertRaisesRegex(AssertionError, "moved under invalid permit"):
            with motion_checked(moved):
                raise RunnerDelay("arm signature", {"cause": "verifier_freshness_refusal"})
        with self.assertRaisesRegex(AssertionError, "binding"):
            with motion_checked(moved):
                raise AssertionError("rejected for binding")

    def test_motion_under_a_refused_packet_fails_unless_the_replay_hold_explains_it(self):
        hold = {"drift_after_refusal": 0.0}
        for drift in (0.0, DRIFT_LIMIT):
            self.assertEqual(refused_motion("arm unsigned", drift), drift)
            self.assertEqual(refused_motion("arm replay", drift, hold), drift)
        # Without the arm replay's hold evidence, motion over the limit is never runner delay.
        with self.assertRaisesRegex(AssertionError, "arm unsigned moved under invalid permit"):
            refused_motion("arm unsigned", DRIFT_LIMIT + .001)
        with self.assertRaises(RunnerDelay) as delay:
            refused_motion("arm replay", DRIFT_LIMIT + .001, hold)
        self.assertEqual(delay.exception.evidence["cause"], "admitted_first_goal_moved_during_duplicate_transit")
        for drift in (float("nan"), float("inf"), None):
            for evidence in (None, hold):
                with self.assertRaisesRegex(AssertionError, "was not measured"):
                    refused_motion("arm replay", drift, evidence)

    def test_positive_runner_delay_lets_visible_controller_events_decide_first(self):
        calls = []
        def nothing_visible():
            calls.append("settled")
        with fail_closed_settled(nothing_visible):
            pass
        self.assertEqual(calls, [])
        # Nothing published yet: the harness-side delay itself is re-raised unchanged.
        delay = RunnerDelay("rust approval", {"cause": "stale_or_expired_rust_approval"})
        with self.assertRaises(RunnerDelay) as raised:
            with fail_closed_settled(nothing_visible):
                raise delay
        self.assertIs(raised.exception, delay)
        self.assertEqual(calls, ["settled"])
        # A premature lock already published fails instead of being retried.
        def premature_lock():
            raise AssertionError("controller locked (expired) before any signed bound could close")
        with self.assertRaisesRegex(AssertionError, "before any signed bound could close"):
            with fail_closed_settled(premature_lock):
                raise delay
        # Failures and timeouts are never intercepted.
        for failure in (AssertionError("timely permit rejected for binding"), TimeoutError("no motion")):
            with self.assertRaises(type(failure)) as raised:
                with fail_closed_settled(nothing_visible):
                    raise failure
            self.assertIs(raised.exception, failure)
        self.assertEqual(calls, ["settled"])

    def test_hold_evidence_requires_an_update_stamp(self):
        with self.assertRaisesRegex(AssertionError, "no controller update stamp"):
            hold_evidence([row(5_100, rejected=5, reason="locked")], 0, 5_000 * MS, "x")


class AttemptLogTest(unittest.TestCase):
    def test_record_is_atomic_finite_and_never_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller-timing.json"
            log = AttemptLog(path, clock=lambda: 7)
            for value in (12.34567, float("nan"), float("inf"), True, "3", 49.9):
                log.sample("renewal_ms", value)
            log.record("arm_positive", 1, "inconclusive", "arm renewal", {"elapsed_ms": float("nan"), "cause": "x"})
            log.record("arm_positive", 2, "passed")
            data = json.loads(path.read_text())
            self.assertEqual(data["series"]["renewal_ms"], [12.346, 49.9])
            self.assertEqual(data["cases"]["arm_positive"], {"attempts": 2, "inconclusive": 1, "outcome": "passed"})
            self.assertIsNone(data["attempts"][0]["evidence"]["elapsed_ms"])
            self.assertEqual((data["scope"], data["limit_ms"], data["max_attempts"], data["complete"]),
                             ("ci_non_blocking_measurement", 50.0, 3, False))
            # An unwritable record is reported, never raised; captured so that the Gazebo job log
            # shows only the real probe's message.
            with contextlib.redirect_stderr(io.StringIO()) as printed:
                AttemptLog(Path(directory) / "missing" / "\0bad").write()
            self.assertTrue(printed.getvalue().startswith("controller timing record not written: "),
                            printed.getvalue())
            self.assertEqual(len(printed.getvalue().splitlines()), 1, printed.getvalue())


class ProbeFunctionsTest(unittest.TestCase):
    """Execute the actual probe functions with the new outcomes."""

    def nodes(self):
        tree = ast.parse((HERE / "controller_probes.py").read_text())
        return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    def renew(self, step_ms, active=True, late=None, in_flight=False):
        nodes = self.nodes()
        clock = {"ns": 10_000 * MS}
        def monotonic_ns():
            clock["ns"] += step_ms * MS
            return clock["ns"]
        def no_event():
            return False
        no_event.pending = False
        sent, samples = [], []
        scope = dict(json=json, now=lambda: 1000, time=SimpleNamespace(monotonic_ns=monotonic_ns),
                     world_snapshot=lambda: {"stamp_ms": 980}, RunnerDelay=RunnerDelay,
                     feed=lambda role, value: {"outcome": {"world_updated": {"stamp_ms": 980}},
                                               "cmd": {"linear": 0, "angular": 0},
                                               "status": {"active": {"id": 1} if active else None}},
                     require_fresh_actuation=require_fresh_actuation,
                     timing=SimpleNamespace(sample=lambda name, value: samples.append((name, value))),
                     token=lambda *args: "token" + json.dumps(args), digest="d" * 64,
                     budget=lambda step, sim: 150 * MS, late=late or no_event, processes={},
                     chain=SimpleNamespace(in_flight=lambda accepted: in_flight, name="chain"),
                     guard=lambda target: {"accepted": 5}, wait_for=self.wait_for,
                     send_timely=lambda packet, permit, stage, chain=None: sent.append((packet, chain)))
        exec(compile(ast.Module(body=[nodes["require_world_accepted"], nodes["settle"], nodes["renew"]],
                                type_ignores=[]), "actual-renewal", "exec"), scope)
        return scope["renew"], sent, samples

    @staticmethod
    def wait_for(predicate, timeout, processes, description, action=None):
        for _ in range(3):
            if predicate():
                return
        raise TimeoutError(description)

    def test_slow_renewal_is_inconclusive_and_never_sent(self):
        renew, sent, samples = self.renew(step_ms=60)
        with self.assertRaises(RunnerDelay) as delay:
            renew()
        self.assertEqual(delay.exception.evidence["cause"], "renewal_round_trip_at_or_over_limit")
        self.assertEqual(sent, [])
        self.assertEqual(samples[0][0], "renewal_ms")
        self.assertGreaterEqual(samples[0][1], 50)

    def test_timely_renewal_signs_its_own_start_origin_on_the_goal_chain(self):
        renew, sent, samples = self.renew(step_ms=5)
        renew()
        self.assertEqual(len(sent), 1)
        packet, chain = sent[0]
        self.assertEqual(chain.name, "chain")
        self.assertEqual(json.loads(packet["heartbeat"][5:]),
                         ["arm", "lease", "d" * 64, 1_000 * MS, 10_005 * MS, 150 * MS])

    def test_no_renewal_while_the_previous_one_is_in_flight(self):
        renew, sent, samples = self.renew(step_ms=5, in_flight=True)
        renew()
        self.assertEqual((sent, samples), ([], []))

    def test_lost_rust_authority_fails_unless_the_controller_shows_a_timing_hold(self):
        renew, sent, _ = self.renew(step_ms=5, active=False)
        with self.assertRaisesRegex(AssertionError, "lost Rust authority"):
            renew()
        self.assertEqual(sent, [])
        def lapsed():
            raise RunnerDelay("arm", {"cause": "governing_lease_lapsed", "reason": "expired"})
        lapsed.pending = False
        renew, sent, _ = self.renew(step_ms=5, active=False, late=lapsed)
        with self.assertRaises(RunnerDelay):
            renew()
        self.assertEqual(sent, [])
        # An event whose hold evidence never arrives is a failure, not runner delay.
        def pending():
            return False
        pending.pending = True
        renew, sent, _ = self.renew(step_ms=5, active=False, late=pending)
        with self.assertRaisesRegex(AssertionError, "without hold evidence"):
            renew()
        self.assertEqual(sent, [])

    def test_late_or_expired_rust_approval_is_inconclusive_but_malformed_fails(self):
        nodes = self.nodes()
        approve = nodes["approve"]
        # Keep the proposal counter writable when executed outside exercise().
        approve.body = [ast.copy_location(ast.Global(names=list(node.names)), node)
                        if isinstance(node, ast.Nonlocal) else node for node in approve.body]
        for step_ms, status, expected in ((60, {"active_expires_ms": 5000, "world_age_ms": 5}, RunnerDelay),
                                          (5, {"active_expires_ms": 900, "world_age_ms": 5}, RunnerDelay),
                                          (5, {"active_expires_ms": 5000, "world_age_ms": 195}, RunnerDelay),
                                          # A malformed response fails, however late it arrived.
                                          (5, {}, StaleActuation), (60, {}, StaleActuation),
                                          (60, {"active_expires_ms": 5000}, StaleActuation),
                                          (5, {"active_expires_ms": 5000, "world_age_ms": 5}, None)):
            clock = {"ns": 0}
            def monotonic_ns(step_ms=step_ms):
                clock["ns"] += step_ms * MS
                return clock["ns"]
            def feed(role, value, status=status):
                if role == "world":
                    return {"outcome": {"world_updated": {"stamp_ms": 980}}}
                return {"cmd": {"linear": .15, "angular": 0}, "status": status}
            samples = []
            scope = dict(json=json, now=lambda: 1000, time=SimpleNamespace(monotonic_ns=monotonic_ns),
                         world_snapshot=lambda: {"stamp_ms": 980}, feed=feed, proposal_id=0,
                         timing=SimpleNamespace(sample=lambda *args: samples.append(args)),
                         require_fresh_actuation=require_fresh_actuation,
                         StaleActuation=StaleActuation, ExpiredActuation=ExpiredActuation,
                         RunnerDelay=RunnerDelay)
            exec(compile(ast.Module(body=[nodes["require_world_accepted"], approve], type_ignores=[]),
                         "actual-approval", "exec"), scope)
            if expected is None:
                self.assertEqual(scope["approve"]({"type": "velocity", "linear": .15})[0], 1000 * MS)
            else:
                with self.assertRaises(expected) as raised:
                    scope["approve"]({"type": "velocity", "linear": .15})
                self.assertIs(type(raised.exception), expected)
            self.assertEqual([name for name, _ in samples], ["world_admission_ms", "actuation_admission_ms"])

    def test_only_the_fixture_issuer_timeout_is_inconclusive(self):
        for failure, expected in ((BridgeFailure("gate response timeout"), RunnerDelay),
                                  (BridgeFailure("gate process exited"), BridgeFailure),
                                  (BridgeFailure("malformed gate response"), BridgeFailure)):
            def request(message, failure=failure):
                raise failure
            scope = dict(json=json, now=lambda: 1000, BridgeFailure=BridgeFailure, RunnerDelay=RunnerDelay,
                         source=SimpleNamespace(sign=lambda role, payload: {"role": role}),
                         bridge=SimpleNamespace(request=request))
            exec(compile(ast.Module(body=[self.nodes()["feed"]], type_ignores=[]), "actual-feed", "exec"), scope)
            with self.assertRaises(expected) as raised:
                scope["feed"]("world", {})
            self.assertIs(type(raised.exception), expected)

    def test_withheld_permit_age_is_still_recorded(self):
        from permit_attempts import check_timely as check, permit_age_ns, permit_fields as fields
        token = "v1:arm-lease:" + "a" * 32 + ":4:990000000:5000000000:1170000000:5180000000:" + "c" * 64 + ":" + "d" * 128
        for wall_ns, expected in ((5_010 * MS, None), (5_070 * MS, RunnerDelay)):
            samples = []
            scope = dict(permit_fields=fields, permit_age_ns=permit_age_ns, check_timely=check, now=lambda: 1_000,
                         time=SimpleNamespace(monotonic_ns=lambda wall_ns=wall_ns: wall_ns),
                         timing=SimpleNamespace(sample=lambda name, value: samples.append((name, value))))
            exec(compile(ast.Module(body=[self.nodes()["require_timely"]], type_ignores=[]), "actual-timely", "exec"), scope)
            if expected is None:
                self.assertEqual(scope["require_timely"](token, "renewal")["seq"], 4)
            else:
                with self.assertRaises(expected):
                    scope["require_timely"](token, "renewal")
            self.assertEqual(samples, [("send_age_ms", (wall_ns - 5_000 * MS) / 1e6)])

    def test_positive_send_needs_the_last_lease_live_and_extends_the_chain(self):
        sent = []
        scope = dict(require_timely=lambda permit, stage: {"kind": permit},
                     require_live=require_live, now=lambda: 1_100,
                     time=SimpleNamespace(monotonic_ns=lambda: 5_100 * MS), send=sent.append)
        exec(compile(ast.Module(body=[self.nodes()["send_timely"]], type_ignores=[]), "actual-send", "exec"), scope)
        chain = GrantChain(grant("base-reset", sim=1_000 * MS, wall=5_000 * MS), 2)
        scope["send_timely"]({"base": 1}, "base-command", "base command", chain)
        self.assertEqual((sent, chain.last()), ([{"base": 1}], {"kind": "base-command"}))
        ended = GrantChain(grant("base-reset", sim=900 * MS, wall=4_900 * MS), 2)
        with self.assertRaises(RunnerDelay) as delay:
            scope["send_timely"]({"base": 2}, "base-command", "base command", ended)
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_ended_before_send")
        self.assertEqual((len(sent), len(ended.grants)), (1, 1))

    def test_exhausted_rust_budget_is_inconclusive_before_signing(self):
        scope = dict(RunnerDelay=RunnerDelay)
        exec(compile(ast.Module(body=[self.nodes()["budget"]], type_ignores=[]), "actual-budget", "exec"), scope)
        self.assertEqual(scope["budget"]({"status": {"world_age_ms": 20}}, 0), 180 * MS)
        with self.assertRaises(RunnerDelay):
            scope["budget"]({"status": {"world_age_ms": 200}}, 0)

    def test_stale_permits_are_signed_at_send_so_only_the_age_bound_can_refuse_them(self):
        from controller_permits import IDLE, MAX_LEASE_NS as SIGNER_LEASE, PermitSigner, SIM_ORDERING_BACKDATE_NS
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "controller.key"
            key.write_text("22" * 32)
            signer = PermitSigner(key, sim_backdate_ns=SIM_ORDERING_BACKDATE_NS)
            signer.observe("arm", {"nonce": "a" * 32})
            goal = permit_fields(signer.sign("arm", "goal", IDLE, 5_000 * MS, 180 * MS, 7_000 * MS))
            # The renewal leaves 100 ms after the goal's origin, when a goal-anchored
            # permit's own ends would already be close.
            scope = dict(now=lambda: 5_100, time=SimpleNamespace(monotonic_ns=lambda: 7_100 * MS),
                         token=lambda target, kind, digest, sim, wall, remaining=SIGNER_LEASE:
                             signer.sign(target, kind, digest, sim, remaining, wall),
                         permit_fields=permit_fields, require_stale_window=require_stale_window,
                         SIM_ORDERING_BACKDATE_NS=SIM_ORDERING_BACKDATE_NS, MAX_LEASE_NS=SIGNER_LEASE)
            exec(compile(ast.Module(body=[self.nodes()["stale_permit"]], type_ignores=[]), "actual-stale", "exec"),
                 scope)
            _, stale = scope["stale_permit"]("arm", "lease", IDLE, "late renewal")
        self.assertEqual((stale["kind"], stale["wall_ns"], stale["sim_ns"]), ("arm-lease", 7_040 * MS, 5_040 * MS))
        self.assertEqual((stale["wall_end_ns"], stale["sim_end_ns"]), (7_240 * MS, 5_240 * MS))
        self.assertGreater(stale["seq"], goal["seq"])
        # 60 ms old at send on both clocks, with 140 ms of its own lease still ahead.
        with self.assertRaises(RunnerDelay):
            check_timely("late renewal", stale, 7_100 * MS, 5_100 * MS)
        require_stale_window("late renewal", stale, 7_100 * MS, 5_100 * MS)

    def test_arm_goal_is_signed_only_after_the_controller_hold_cutoff(self):
        clock = {"ms": 1_000}
        waits, approved = [], []
        def now():
            return clock["ms"]
        def wait_for(predicate, timeout, processes, description, action=None):
            waits.append(description)
            while not predicate():
                clock["ms"] += 1
        def approve(action):
            approved.append(now())
            raise RuntimeError("stop after approval")
        scope = dict(guard=lambda target: {"cutoff_ms": 1_004}, now=now, wait_for=wait_for, processes={},
                     approve=approve, SIM_ORDERING_BACKDATE_NS=10 * MS,
                     world=SimpleNamespace(arm_positions=lambda: [0.0] * 4))
        exec(compile(ast.Module(body=[self.nodes()["arm_packet"]], type_ignores=[]), "actual-arm-packet", "exec"),
             scope)
        with self.assertRaisesRegex(RuntimeError, "stop after approval"):
            scope["arm_packet"]()
        # The signed origin (approval time minus the 10 ms backdate) is not before the cutoff.
        self.assertEqual(approved, [1_014])
        self.assertEqual(waits, ["arm goal origin at or after the controller hold cutoff"])

    def base_positive(self, base, second_approval=None):
        """The actual base_positive with its real fail-closed checks against `base`."""
        nodes = self.nodes()
        approvals = []
        def base_packet():
            approvals.append(base.clock["ns"])
            if second_approval is not None and len(approvals) == 2:
                second_approval()
            return {"base": {"permit": "permit"}}
        def send_timely(packet, permit, stage, chain=None):
            fields = grant("base-command", sim=base.clock["ns"] - 4_000 * MS, wall=base.clock["ns"])
            chain.sent(fields)
            base.send(fields)
        reset = grant("base-reset", sim=1_000 * MS, wall=5_000 * MS)    # ends 5180 ms
        scope = dict(json=json, time=SimpleNamespace(monotonic_ns=lambda: base.clock["ns"]),
                     world=SimpleNamespace(base_guard_states=base.rows, guard_states=[],
                                           pose=lambda: [base.cm / 100, 0.0]),
                     reset=lambda: {"base": {"fields": reset, "accepted": 3, "unlocked_wall_ns": 5_005 * MS}},
                     GrantChain=GrantChain, require_no_fail_closed=require_no_fail_closed,
                     fail_closed_settled=fail_closed_settled, processes={}, wait_for=base.wait_for,
                     base_packet=base_packet, send_timely=send_timely)
        names = ("telemetry", "guard", "since", "fail_closed_check", "settle", "positive_motion", "base_positive")
        exec(compile(ast.Module(body=[nodes[name] for name in names], type_ignores=[]),
                     "actual-base-positive", "exec"), scope)
        return scope["base_positive"], approvals

    def test_positive_motion_waits_for_the_last_permit_and_judges_it(self):
        # The base passes 3 cm while the third command is still in flight.
        base = ScriptedBase(["admit", "admit", "admit"])
        run, approvals = self.base_positive(base)
        self.assertEqual(run(), {"ok": True, "moved_m": .06})
        self.assertEqual((len(approvals), base.queue, base.state["accepted"]), (3, [], 6))
        # Its non-timing refusal still fails, although the base already passed the threshold.
        run, _ = self.base_positive(ScriptedBase(["admit", "admit", "binding"]))
        with self.assertRaisesRegex(AssertionError, "timely permit rejected for binding"):
            run()
        # Timing outcomes for it are inconclusive, never a pass: a freshness refusal, or a
        # dropped permit whose governing lease then lapses at its end.
        for outcome, cause in (("freshness", "verifier_freshness_refusal"), ("drop", "governing_lease_lapsed")):
            run, _ = self.base_positive(ScriptedBase(["admit", "admit", outcome]))
            with self.assertRaises(RunnerDelay) as delay:
                run()
            self.assertEqual(delay.exception.evidence["cause"], cause, outcome)

    def test_positive_case_fails_when_the_controller_counts_more_admissions_than_permits_sent(self):
        # The controller counts the second command twice, so the harness reads the
        # third as already counted. The base moves and nothing is refused, yet the count (7) runs
        # past the reset and three commands this harness sent (6): never a pass.
        base = ScriptedBase(["admit", "admit twice", "admit"])
        run, approvals = self.base_positive(base)
        with self.assertRaisesRegex(AssertionError, "signed base physically moves: controller admission count 7 "
                                                    "does not match the 4 permits this harness sent"):
            run()
        self.assertEqual((len(approvals), base.state["rejected"]), (3, 4))

    def test_runner_delay_in_the_positive_action_first_lets_published_events_decide(self):
        # The controller locks 160 ms before its lease could end while the next approval runs,
        # and that approval then comes back late. The visible premature lock fails the case.
        base = ScriptedBase(["admit", "admit"])
        delay = RunnerDelay("rust approval", {"cause": "stale_or_expired_rust_approval"})
        def locked_then_late():
            base.state.update(reason="expired", holding=True)
            base.publish()
            raise delay
        run, _ = self.base_positive(base, locked_then_late)
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before any signed bound could close"):
            run()
        # With nothing published, the approval's own delay is the inconclusive outcome.
        def late():
            raise delay
        run, _ = self.base_positive(ScriptedBase(["admit", "admit"]), late)
        with self.assertRaises(RunnerDelay) as raised:
            run()
        self.assertIs(raised.exception, delay)

    def arm_positive_until_renewals(self, ticks):
        """The actual arm_positive() up to its renewals. The reset lease ends at 1175 ms sim /
        5180 ms wall and the goal leaves at 5040 ms; each wait poll advances the clock 10 ms
        and publishes ticks[clock_ms]. An admitted goal raises ReachedRenewals."""
        clock = {"ns": 5_040 * MS}
        rows = [(0, arm(5_005, 1_005))]
        reset_grant = grant("arm-reset", sim=995 * MS, wall=5_000 * MS)
        goal = grant("arm-goal", sim=1_020 * MS, wall=5_030 * MS)
        def wait_for(predicate, timeout, processes, description, action=None):
            for _ in range(int(timeout * 100)):
                clock["ns"] += 10 * MS
                rows.extend((0, value) for value in ticks.get(clock["ns"] // MS, ()))
                if predicate():
                    return
            raise TimeoutError(description)
        def positive_motion(*args):
            raise ReachedRenewals()
        scope = dict(json=json, time=SimpleNamespace(monotonic_ns=lambda: clock["ns"]), processes={},
                     wait_for=wait_for, world=SimpleNamespace(guard_states=rows, base_guard_states=[],
                                                              arm_positions=lambda: [0.0] * 4),
                     reset=lambda: {"arm": {"fields": reset_grant, "accepted": 3, "unlocked_wall_ns": 5_005 * MS}},
                     GrantChain=GrantChain, require_no_fail_closed=require_no_fail_closed,
                     fail_closed_settled=fail_closed_settled, goal_counted=goal_counted,
                     arm_packet=lambda: ({"arm": {"permit": "goal"}}, "d" * 64),
                     send_timely=lambda packet, permit, stage, chain=None: chain.sent(goal),
                     positive_motion=positive_motion)
        names = ("telemetry", "guard", "since", "fail_closed_check", "settle", "require_counted", "arm_positive")
        exec(compile(ast.Module(body=[self.nodes()[name] for name in names], type_ignores=[]),
                     "actual-arm-positive", "exec"), scope)
        return scope["arm_positive"]

    def test_a_valid_arm_goal_the_controller_never_counts_fails_even_after_a_lapse_at_the_lease_end(self):
        # Goals travel on a reliable action whose ingress counts every goal, even one that arrives
        # after a lapse, so a valid goal that is never counted is a controller fault.
        # The reset lease (ends 1175 ms sim / 5180 ms wall) lapses at its end; the arm holds.
        lapse = {5_180: [arm(5_180, 1_170, reason="expired")],
                 5_190: [held(5_190, 1_180, stop_ms=5_181, cutoff_ms=1_176, rejected=4, reason="expired")]}
        late_goal = {5_300: [held(5_300, 1_290, stop_ms=5_181, cutoff_ms=1_176, reason="rejected")]}
        # The positive control's goal.
        with self.assertRaises(ReachedRenewals):
            self.arm_positive_until_renewals({5_050: [arm(5_050, 1_040, accepted=4)]})()
        with self.assertRaisesRegex(AssertionError, "signed arm physically moves: the controller never counted "
                                                    "a valid arm goal"):
            self.arm_positive_until_renewals(lapse)()
        # Counted after the lapse, at the ingress (`rejected`): runner delay, never a pass.
        with self.assertRaises(RunnerDelay) as delay:
            self.arm_positive_until_renewals({**lapse, **late_goal})()
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
        # The 1 s admission wait: a goal counted only later still fails.
        with self.assertRaisesRegex(AssertionError, "never counted a valid arm goal"):
            self.arm_positive_until_renewals({**lapse, 6_300: late_goal[5_300]})()
        # The late-renewal case's goal, whether or not its lapse shows inside the 0.2 s wait.
        for ticks in ({}, lapse):
            with self.assertRaisesRegex(AssertionError, "arm renewal_delay goal: the controller never counted "
                                                        "a valid arm goal"):
                self.late_renewal_until_send(ticks=ticks)()
        with self.assertRaises(RunnerDelay) as delay:
            self.late_renewal_until_send(ticks={**lapse, **late_goal})()
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
        # The arm replay's first copy (wall end 5250 ms); the base's best-effort command is not checked.
        first = "v1:{}-{}:" + NONCE + ":2:1030000000:5050000000:1230000000:5250000000:" + "c" * 64 + ":" + "d" * 128
        arm_lapse = [arm(5_182, 1_181, reason="expired"),
                     held(5_190, 1_182, stop_ms=5_181, cutoff_ms=1_181, rejected=4, reason="expired")]
        def replay(target, rows):
            return self.negative_case("replay", permit=first.format(target, "goal" if target == "arm" else "command"),
                                      after_send=[rows], target=target)()
        with self.assertRaisesRegex(AssertionError, "arm replay first packet: the controller never counted "
                                                    "a valid arm goal"):
            replay("arm", arm_lapse)
        with self.assertRaises(RunnerDelay) as delay:
            replay("arm", arm_lapse + [held(5_240, 1_230, stop_ms=5_181, cutoff_ms=1_181, reason="rejected")])
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")
        with self.assertRaises(RunnerDelay) as delay:
            replay("base", [row(5_165, reason="expired", holding=True)])
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed")

    def negative_case(self, case, build=None, stale_permit=None, after_send=(), permit=None, target="base",
                      at_wait=None):
        """The actual negative() for `target`, against scripted telemetry, through its refusal judgement.

        The reset lease ends at 1180 ms sim / 5180 ms wall; the harness clock reads 5050 ms
        (1040 ms sim). build() runs inside the packet build and stale_permit() replaces the
        stale-permit signer. The n-th send publishes the rows in after_send[n], and the wait
        named d in at_wait publishes at_wait[d] when it begins; run.waits lists every wait as
        (description, timeout). A refusal judged a pass goes on to the recovery packet, which
        raises ReachedRecovery; neither the drift check nor the arm's joint history finds motion.
        """
        rows = [(0, row(5_005) if target == "base" else arm(5_005, 1_000))]
        record = NegativeRefusalTest.reset_record(grant(target + "-reset", sim=1_000 * MS, wall=5_000 * MS), 5_005)
        published = iter(after_send)
        waits = []
        def wait_for(predicate, timeout, processes, description, action=None):
            waits.append((description, timeout))
            rows.extend((0, value) for value in (at_wait or {}).get(description, ()))
            return self.wait_for(predicate, timeout, processes, description, action)
        def base_packet():
            if build is not None:
                build(rows)
            return {"base": {"permit": permit or "v1:base-command:" + NONCE + ":2:1:2:3:4:" + "c" * 64 + ":" + "d" * 128}}
        def arm_packet():
            if build is not None:
                build(rows)
            return ({"arm": {"points": [{"positions": [0.0] * 4}], "permit": permit or (
                "v1:arm-goal:" + NONCE + ":2:1030000000:5040000000:1210000000:5220000000:" + "c" * 64 + ":"
                + "d" * 128)}}, "c" * 64)
        def send(packet):
            rows.extend((0, value) for value in next(published))
        def recover(target, stage):
            raise ReachedRecovery(stage)
        seconds = iter(range(10**6))
        scope = dict(reset=lambda: {"base": record, "arm": record}, GrantChain=GrantChain, PENDING=PENDING,
                     reset_lease_events=reset_lease_events, fail_closed_settled=fail_closed_settled,
                     goal_counted=goal_counted, precondition_miss=precondition_miss,
                     VERIFIER_WINDOW_NS=VERIFIER_WINDOW_NS,
                     world=SimpleNamespace(base_guard_states=rows if target == "base" else [],
                                           guard_states=rows if target == "arm" else [], pose=lambda: [5.0, 5.0],
                                           arm_positions=lambda: [0.0] * 4),
                     time=SimpleNamespace(monotonic_ns=lambda: 5_050 * MS, monotonic=lambda: float(next(seconds))),
                     now=lambda: 1_040, processes={}, wait_for=wait_for, base_packet=base_packet,
                     arm_packet=arm_packet, stale_permit=lambda *args: stale_permit(rows), send=send, recover=recover,
                     copy=copy, hashlib=hashlib, json=json, signer=SimpleNamespace(challenges={target: NONCE}),
                     permit_fields=permit_fields, require_timely=lambda permit, stage: permit_fields(permit),
                     require_fresh_witness=require_fresh_witness, require_live=require_live,
                     require_no_fail_closed=require_no_fail_closed, motion_checked=motion_checked,
                     refusal=refusal, negative_timing=negative_timing, NEGATIVE_REASONS=NEGATIVE_REASONS,
                     require_refused_inside_lease=require_refused_inside_lease,
                     require_refused_inside_stale_window=require_refused_inside_stale_window,
                     refused_motion=refused_motion, stopped=lambda: True,
                     # The cross-target signer, and the arm's hold and joint history (no motion).
                     token=lambda *args: "v1:base-command:" + NONCE + ":9:1:2:3:4:" + "c" * 64 + ":" + "d" * 128,
                     SIM_ORDERING_BACKDATE_NS=10 * MS, DRIFT_LIMIT=DRIFT_LIMIT, refusal_hold=refusal_hold,
                     motion_after=motion_after, joint_history=lambda: [(1_000 * MS, [0.0] * 4), (1_300 * MS, [0.0] * 4)])
        names = ("telemetry", "guard", "since", "wait_row", "wait_judged", "fail_closed_check", "settle",
                 "require_counted", "arm_hold", "negative")
        exec(compile(ast.Module(body=[self.nodes()[name] for name in names], type_ignores=[]),
                     "actual-negative", "exec"), scope)
        def run():
            return scope["negative"](target, case)
        run.waits = waits
        return run

    def test_harness_delay_before_a_negative_packet_leaves_judges_the_reset_lease_first(self):
        delay = RunnerDelay("rust approval", {"cause": "stale_or_expired_rust_approval"})
        def approval(*published):
            def build(rows):
                rows.extend((0, value) for value in published)
                raise delay
            return build
        # The reset lease lapses 95 ms early while the approval runs late.
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before any signed bound could close"):
            self.negative_case("unsigned", approval(row(5_085, reason="expired", holding=True)))()
        # A lapse inside the allowance is the reset lease's own end: still inconclusive.
        with self.assertRaises(RunnerDelay) as raised:
            self.negative_case("unsigned", approval(row(5_160, reason="expired", holding=True)))()
        self.assertEqual(raised.exception.evidence["cause"], "governing_lease_lapsed")
        # Nothing was sent, so even a counted `freshness` refusal fails.
        with self.assertRaisesRegex(AssertionError, "controller state changed before the negative packet"):
            self.negative_case("unsigned", approval(row(5_080, rejected=5, reason="freshness", holding=True)))()
        # Nothing visible: the harness's own delay is the inconclusive outcome, unchanged.
        for published in ((), (row(5_025), row(5_045))):
            with self.assertRaises(RunnerDelay) as raised:
                self.negative_case("unsigned", approval(*published))()
            self.assertIs(raised.exception, delay)
        # After the live witness (5040 ms), a delayed stale-permit signer is judged the same way.
        stale = RunnerDelay("base delay", {"cause": "stale_permit_ends_passed_before_send"})
        def signer(*published):
            def sign(rows):
                rows.extend((0, value) for value in published)
                raise stale
            return sign
        def live(rows):
            rows.append((0, row(5_040)))
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before any signed bound could close"):
            self.negative_case("delay", live, signer(row(5_045, reason="expired", holding=True)))()
        with self.assertRaises(RunnerDelay) as raised:
            self.negative_case("delay", live, signer())()
        self.assertIs(raised.exception, stale)

    def test_pre_send_witness_must_still_show_the_resets_counters(self):
        # An admission (or a rejection) published between the reset and the pre-send
        # row, still unlocked under the same nonce. Nothing was sent, so it fails at once instead of
        # becoming the baseline that the negative packet's refusal is counted against.
        for changed in (row(5_040, accepted=4), row(5_040, rejected=5)):
            refused = dict(changed, published_wall_ns=5_120 * MS, rejected=changed["rejected"] + 1,
                           reason="binding", holding=True)
            with self.assertRaisesRegex(AssertionError, "controller state changed before the negative packet"):
                self.negative_case("unsigned", lambda rows, changed=changed: rows.append((0, changed)),
                                   after_send=[[refused]])()
        # With the reset's own counters, the same live row is the witness and the refusal counts.
        with self.assertRaises(ReachedRecovery):
            self.negative_case("unsigned", lambda rows: rows.append((0, row(5_040))),
                               after_send=[[row(5_120, rejected=5, reason="binding", holding=True)]])()

    def test_negative_judges_a_lapse_before_its_refusal_against_the_lease_it_relied_on(self):
        # The actual negative() through its refusal judgement. A refusal replaces `expired` on a
        # lapsed guard, so only the earlier lapse row tells a premature lock apart, and only
        # against the lease the controller relied on (the governing argument).
        refused = row(5_120, rejected=5, reason="binding", holding=True)
        with self.assertRaises(ReachedRecovery):
            self.negative_case("unsigned", after_send=[[refused]])()
        # The reset lease (wall end 5180 ms) lapsing 80 ms early fails.
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before the governing lease could end"):
            self.negative_case("unsigned", after_send=[[row(5_100, reason="expired", holding=True), refused]])()
        # At its end (inside the 20 ms allowance) the lapse is inconclusive, after the drift check.
        with self.assertRaises(RunnerDelay) as delay:
            self.negative_case("unsigned", after_send=[[row(5_160, reason="expired", holding=True),
                                                        row(5_170, rejected=5, reason="binding", holding=True)]])()
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed_before_refusal")
        # The replay duplicate relies on the admitted first copy (wall end 5250 ms), not on the
        # superseded reset lease: a lapse at 5190 ms is 60 ms early for it, though inside the
        # reset lease's allowance.
        first = ("v1:base-command:" + NONCE + ":2:1030000000:5050000000:1230000000:5250000000:"
                 + "c" * 64 + ":" + "d" * 128)
        admitted = row(5_050, accepted=4)
        duplicate_refused = row(5_200, rejected=5, accepted=4, reason="sequence", holding=True)
        with self.assertRaises(ReachedRecovery):
            self.negative_case("replay", permit=first, after_send=[[admitted], [duplicate_refused]])()
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before the governing lease could end"):
            self.negative_case("replay", permit=first, after_send=[
                [admitted], [row(5_190, accepted=4, reason="expired", holding=True), duplicate_refused]])()

    def test_an_arm_goal_the_controller_never_counts_fails_even_after_a_lapse_at_the_lease_end(self):
        # Goals travel on a reliable action whose ingress counts every goal, even one that arrives
        # after a lapse, so an inconclusive outcome still needs the goal's counted refusal and prompt
        # hold. Here the reset lease (ends 1180 ms sim / 5180 ms wall) lapses at its end.
        lapse = [arm(5_182, 1_181, reason="expired"),
                 held(5_190, 1_182, stop_ms=5_181, cutoff_ms=1_181, rejected=4, reason="expired")]
        stale = "v1:arm-goal:" + NONCE + ":3:980000000:4990000000:1180000000:5190000000:" + "c" * 64 + ":" + "d" * 128
        for case in ("unsigned", "altered", "signature", "delay", "target"):
            def run(rows, case=case):
                return self.negative_case(case, stale_permit=lambda _: (stale, permit_fields(stale)),
                                          after_send=[rows], target="arm")()
            # Never counted: neither admitted nor refused, so nothing latched. Never runner delay.
            with self.assertRaisesRegex(AssertionError, "arm " + case + ": no controller refusal and hold bound"):
                run(lapse)
            # A slow goal that reached the lapsed controller is refused at its ingress: inconclusive.
            with self.assertRaises(RunnerDelay) as delay:
                run(lapse + [held(5_240, 1_230, stop_ms=5_181, cutoff_ms=1_181, reason="rejected")])
            self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed_before_refusal", case)
        # A latched refusal with a prompt hold is judged a pass and reaches the recovery packet.
        with self.assertRaises(ReachedRecovery):
            self.negative_case("unsigned", target="arm", after_send=[[
                arm(5_100, 1_090, rejected=5, reason="binding"),
                held(5_110, 1_100, stop_ms=5_101, cutoff_ms=1_091, reason="binding")]])()
        # Base commands travel on a best-effort topic: one that the base never counts stays
        # inconclusive once the reset lease lapses at its end (after the drift check).
        with self.assertRaises(RunnerDelay) as delay:
            self.negative_case("unsigned", after_send=[[row(5_165, reason="expired", holding=True)]])()
        self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed_before_refusal")

    def test_a_refused_arm_goal_counted_after_the_drift_observation_is_still_inconclusive(self):
        # A slow goal reaches the arm after the reset lease (ends 1180 ms sim / 5180 ms wall)
        # lapsed at its end, and its ingress refusal (`rejected`) shows only once the 0.3 s drift
        # observation is over. arm_hold gives it the conclusive path's 2 s, so this is runner
        # delay; a goal that is never counted still fails (above).
        lapse = [arm(5_182, 1_181, reason="expired"),
                 held(5_190, 1_182, stop_ms=5_181, cutoff_ms=1_181, rejected=4, reason="expired")]
        counted = held(5_600, 1_590, stop_ms=5_181, cutoff_ms=1_181, reason="rejected")
        stale = "v1:arm-goal:" + NONCE + ":3:980000000:4990000000:1180000000:5190000000:" + "c" * 64 + ":" + "d" * 128
        for case in ("unsigned", "altered", "signature", "delay", "target"):
            shown = "arm " + case + " counted refusal and prompt hold"
            run = self.negative_case(case, stale_permit=lambda _: (stale, permit_fields(stale)), after_send=[lapse],
                                     target="arm", at_wait={shown: [counted]})
            with self.assertRaises(RunnerDelay) as delay:
                run()
            self.assertEqual(delay.exception.evidence["cause"], "governing_lease_lapsed_before_refusal", case)
            waits = [description for description, _ in run.waits]
            self.assertLess(waits.index("negative control observation"), waits.index(shown), case)
            self.assertEqual(run.waits[-1], (shown, 2), case)

    def recover_until_send(self, target, published):
        """The actual recover() up to its send. The controller latched a `binding` refusal; the
        first approval publishes `published` controller rows and then comes back late."""
        rows = [(0, row(5_200, rejected=5, reason="binding", holding=True))]
        delay = RunnerDelay("rust approval", {"cause": "stale_or_expired_rust_approval"})
        def approve(action):
            rows.extend((0, value) for value in published)
            raise delay
        scope = dict(json=json, fail_closed_settled=fail_closed_settled, new_issuer=lambda: None, approve=approve,
                     world=SimpleNamespace(base_guard_states=rows, guard_states=rows))
        names = ("telemetry", "guard", "since", "recover")
        exec(compile(ast.Module(body=[self.nodes()[name] for name in names], type_ignores=[]),
                     "actual-recover", "exec"), scope)
        return (lambda: scope["recover"](target, target + " unsigned")), delay

    def test_harness_delay_before_the_recovery_packet_leaves_judges_the_latch_first(self):
        for target in ("base", "arm"):
            # Nothing reaches the latched controller before the recovery packet leaves: only
            # time may pass there, and the approval's own delay is the inconclusive outcome.
            for published in ((), (row(5_220, rejected=5, reason="binding", holding=True),)):
                run, delay = self.recover_until_send(target, published)
                with self.assertRaises(RunnerDelay) as raised:
                    run()
                self.assertIs(raised.exception, delay)
            # An automatic recovery published while the approval ran late fails at
            # once, also when the controller locked again before the delay surfaced.
            for published in ((row(5_220, rejected=5, reason="binding"),),
                              (row(5_220, rejected=5, reason="binding"), row(5_240, rejected=5, reason="expired",
                                                                             holding=True)),
                              (row(5_220, rejected=5, accepted=4, holding=True),),
                              (dict(row(5_220, rejected=5, reason="binding", holding=True), nonce="c" * 32),),
                              (row(5_220, rejected=6, reason="binding", holding=True),)):
                run, _ = self.recover_until_send(target, published)
                with self.assertRaisesRegex(AssertionError, "automatic recovery before the " + target
                                            + " unsigned recovery packet left"):
                    run()

    def late_renewal_until_send(self, before_send=None, ticks=None):
        """The actual late_renewal() up to its send. The goal (ends 1200 ms sim / 5210 ms wall)
        is admitted and moving at 5070 ms; before_send(rows) replaces the pre-send witness check.
        ticks ({clock_ms: rows}) replaces that telemetry; the arm then never moves."""
        clock, joint = {"ns": 5_040 * MS}, {"q": 0.0}
        rows = [(0, dict(arm(5_005, 1_005), active_digest="0" * 64))]
        reset_grant = grant("arm-reset", sim=995 * MS, wall=5_000 * MS)
        goal = grant("arm-goal", sim=1_020 * MS, wall=5_030 * MS)
        def wait_for(predicate, timeout, processes, description, action=None):
            for _ in range(int(timeout * 100)):
                clock["ns"] += 10 * MS
                if ticks is not None:
                    rows.extend((0, value) for value in ticks.get(clock["ns"] // MS, ()))
                elif clock["ns"] == 5_070 * MS:
                    joint["q"] = .01
                    rows.append((0, goal_row(5_070, 1_060)))
                if predicate():
                    return
            raise TimeoutError(description)
        scope = dict(json=json, time=SimpleNamespace(monotonic_ns=lambda: clock["ns"]),
                     now=lambda: clock["ns"] // MS - 4_010, processes={}, wait_for=wait_for,
                     world=SimpleNamespace(guard_states=rows, base_guard_states=[], primary_joint=lambda: joint["q"]),
                     reset=lambda: {"arm": {"fields": reset_grant, "accepted": 3, "unlocked_wall_ns": 5_005 * MS}},
                     GrantChain=GrantChain, require_no_fail_closed=require_no_fail_closed,
                     fail_closed_settled=fail_closed_settled, goal_counted=goal_counted, RunnerDelay=RunnerDelay,
                     VERIFIER_WINDOW_NS=VERIFIER_WINDOW_NS, require_live=require_live,
                     arm_packet=lambda: ({"arm": {"permit": "goal"}}, "d" * 64),
                     send_timely=lambda packet, permit, stage, chain=None: chain.sent(goal),
                     stale_permit=lambda *args: ("stale", dict(goal, seq=3)),
                     require_fresh_witness=lambda stage, published, sent: before_send(rows, goal_row))
        names = ("telemetry", "guard", "since", "wait_row", "fail_closed_check", "settle", "require_counted",
                 "late_renewal")
        exec(compile(ast.Module(body=[self.nodes()[name] for name in names], type_ignores=[]),
                     "actual-late-renewal", "exec"), scope)
        return scope["late_renewal"]

    def test_harness_delay_before_the_late_renewal_leaves_judges_the_goal_first(self):
        delay = RunnerDelay("arm renewal_delay", {"cause": "pre_send_telemetry_aged"})
        # The goal lease lapses 80 ms before its wall end and the arm holds,
        # then the pre-send witness check reports a runner delay.
        def premature(rows, moving):
            rows.append((0, moving(5_131, 1_121, reason="expired", holding=True, stop_ms=5_130, cutoff_ms=1_120)))
            raise delay
        with self.assertRaisesRegex(AssertionError, r"locked \(expired\) before any signed bound could close"):
            self.late_renewal_until_send(premature)()
        def nothing(rows, moving):
            raise delay
        with self.assertRaises(RunnerDelay) as raised:
            self.late_renewal_until_send(nothing)()
        self.assertIs(raised.exception, delay)

    def test_goal_not_seen_moving_is_judged_before_it_counts_as_runner_delay(self):
        # The goal is admitted (accepted 4) but the arm never moves inside the 0.2 s wait (to 5240 ms).
        admitted = {5_050: [goal_row(5_050, 1_040)]}
        # (a) The goal lease lapses 150 ms early; its hold stamps show only after the wait ended.
        lapse = {**admitted, 5_060: [goal_row(5_060, 1_050, reason="expired", holding=True)],
                 5_250: [goal_row(5_250, 1_240, reason="expired", holding=True, stop_ms=5_061, cutoff_ms=1_055)]}
        with self.assertRaisesRegex(AssertionError, r"goal: controller locked \(expired\) before any signed bound"):
            self.late_renewal_until_send(ticks=lapse)()
        # (b) One more admission than the goal, with no fail-closed event.
        with self.assertRaisesRegex(AssertionError, "controller admitted unexpected traffic"):
            self.late_renewal_until_send(ticks={**admitted, 5_060: [goal_row(5_060, 1_050, accepted=5)]})()
        # (c) Only time passed: the attempt is inconclusive.
        with self.assertRaises(RunnerDelay) as delay:
            self.late_renewal_until_send(ticks=admitted)()
        self.assertEqual(delay.exception.evidence, {"cause": "goal_motion_not_witnessed_inside_goal_lease",
                                                    "accepted_delta": 1})
        # (d) The controller counts the goal only after that wait and settle()'s poll (5250 ms), so the
        # event it counted is judged too, once counted. A refusal for another reason, published before
        # the reset lease (ends 1175 ms sim / 5180 ms wall) could end, fails instead of being retried.
        def refused(reason):
            return arm(5_175, 1_165, rejected=5, reason=reason, holding=True, stop_ms=5_176, cutoff_ms=1_166)
        for reason in ("binding", "signature", "sequence"):
            for shown in (5_260, 5_300):
                with self.assertRaisesRegex(AssertionError, "goal: timely permit rejected for " + reason):
                    self.late_renewal_until_send(ticks={shown: [refused(reason)]})()
        # So does a lapse 70 ms before the reset lease could end, shown before the goal's ingress refusal.
        early = {5_260: [held(5_110, 1_100, stop_ms=5_101, cutoff_ms=1_091, rejected=4, reason="expired")],
                 5_300: [held(5_200, 1_190, stop_ms=5_101, cutoff_ms=1_091, reason="rejected")]}
        with self.assertRaisesRegex(AssertionError, r"goal: controller locked \(expired\) before any signed bound"):
            self.late_renewal_until_send(ticks=early)()
        # A late `freshness` refusal of the goal, or its late admission, stays inconclusive, never a pass.
        with self.assertRaises(RunnerDelay) as delay:
            self.late_renewal_until_send(ticks={5_300: [refused("freshness")]})()
        self.assertEqual(delay.exception.evidence["cause"], "verifier_freshness_refusal")
        with self.assertRaises(RunnerDelay) as delay:
            self.late_renewal_until_send(ticks={5_300: [goal_row(5_295, 1_285)]})()
        self.assertEqual(delay.exception.evidence, {"cause": "goal_motion_not_witnessed_inside_goal_lease",
                                                    "accepted_delta": 0})

    def test_motion_checks_fail_on_motion_except_after_the_replay_duplicates_refusal(self):
        hold = {"drift_after_refusal": 0.0}
        def load(path, **scope):
            exec(compile(ast.Module(body=[nested(*path)], type_ignores=[]), "actual-" + "-".join(path), "exec"),
                 scope)
            return scope[path[-1]]
        quiet = dict(wait_for=lambda *args, **kwargs: None, stopped=None, processes={},
                     time=SimpleNamespace(monotonic=lambda: 0.0), refused_motion=refused_motion)
        for target, case in PERMIT_NEGATIVES[:-1]:
            judged = []
            def counted(*args):
                judged.append(args[1:])
                return hold
            def check(moved, target=target, case=case, hold_check=counted):
                return load(("negative", "check_motion"), **quiet, target=target, case=case,
                           stage=target + " " + case, pose=[0.0, 0.0], joints=[0.0] * 4, mark=7, hold_after=0,
                           world=SimpleNamespace(pose=lambda: [moved, 0.0], arm_positions=lambda: [moved, 0, 0, 0]),
                           arm_hold=hold_check, negative_witness={"sent_wall_ns": 1, "rejected_before": 4})()
            replay = (target, case) == ("arm", "replay")
            # Every arm negative is judged with its own counted refusal and prompt hold, from the
            # telemetry mark taken before the packet left.
            self.assertEqual(check(.01), (.01, hold if target == "arm" else None), (target, case))
            self.assertEqual(judged, [(7, 1, 4, 0)] if target == "arm" else [], (target, case))
            with self.assertRaises(RunnerDelay if replay else AssertionError, msg=(target, case)):
                check(DRIFT_LIMIT + .001)
            if target == "arm":
                # A goal the controller never counted fails on every path, whatever the drift.
                def uncounted(*args, stage=target + " " + case):
                    raise AssertionError(stage + ": no controller refusal and hold bound the arm's motion")
                with self.assertRaisesRegex(AssertionError, "no controller refusal and hold", msg=(target, case)):
                    check(0.0, hold_check=uncounted)
        for moved in (.01, DRIFT_LIMIT + .001):
            positions = iter(([0.0] * 4, [moved, 0.0, 0.0, 0.0]))
            def late_check():
                return load(("late_renewal", "check_motion"), **quiet, stage="arm renewal_delay", mark=0, sent=1,
                           hold_after=0, world=SimpleNamespace(arm_positions=lambda: next(positions)),
                           arm_hold=lambda *args: hold, negative_witness={"rejected_before": 4})()
            if moved <= DRIFT_LIMIT:
                self.assertEqual(late_check(), (moved, hold))
            else:
                with self.assertRaisesRegex(AssertionError, "arm renewal_delay moved under invalid permit"):
                    late_check()
        for target in ("base", "arm"):
            for holding, moved, recovered in ((True, .01, False), (False, 0.0, True), (True, DRIFT_LIMIT + .001, True)):
                def recovery_check(holding=holding, moved=moved, target=target):
                    return load(("recover", "check_motion"), **quiet, math=math, DRIFT_LIMIT=DRIFT_LIMIT,
                               target=target, guard=lambda target: {"holding": holding},
                               recovery_pose=[0.0, 0.0], recovery_joints=[0.0] * 4,
                               world=SimpleNamespace(pose=lambda: [moved, 0.0], arm_positions=lambda: [moved, 0, 0, 0]))()
                if recovered:
                    with self.assertRaisesRegex(AssertionError, "automatic recovery"):
                        recovery_check()
                else:
                    self.assertEqual(recovery_check(), moved)
        refused = arm(5_130, 1_120, rejected=5, reason="binding")
        prompt = held(5_140, 1_130, 5_131, 1_130, reason="binding")
        starts, judged_waits = [], []
        def arm_hold(found, moved):
            def motion_after(samples, start_ns, final_positions):
                starts.append(start_ns)
                return moved
            def wait_judged(target, mark, judge, timeout, description):
                judged_waits.append((target, mark, timeout, description))
                verdict = judge([])
                if verdict is None:
                    raise TimeoutError(description)
                return verdict
            return load(("arm_hold",), json=json, DRIFT_LIMIT=DRIFT_LIMIT, joint_history=lambda: [],
                        world=SimpleNamespace(arm_positions=lambda: [0.0] * 4), wait_judged=wait_judged,
                        refusal_hold=lambda *args: found, motion_after=motion_after)("arm replay", 7, 1, 4, 0)
        for found, moved, failure in ((None, 0.0, "no controller refusal and hold"),
                                      ((refused, prompt), DRIFT_LIMIT + .001, "moved after the controller refused"),
                                      ((refused, prompt), None, "moved after the controller refused")):
            with self.assertRaisesRegex(AssertionError, failure):
                arm_hold(found, moved)
        starts.clear()
        self.assertEqual(arm_hold((refused, prompt), .01),
                         {"refusal_stamp_ms": 1_120, "hold_cutoff_ms": 1_130, "drift_after_refusal": .01})
        earlier = held(5_140, 1_130, 5_111, 1_110, reason="binding")
        self.assertEqual(arm_hold((refused, earlier), .01)["hold_cutoff_ms"], 1_110)
        # Motion counts from the refusal row's update (1120 ms), or from the hold if it began earlier.
        self.assertEqual(starts, [1_120 * MS, 1_110 * MS])
        # The arm's telemetry since the caller's mark gets the conclusive path's 2 s to show them.
        self.assertEqual(set(judged_waits), {("arm", 7, 2, "arm replay counted refusal and prompt hold")})


class ProbeStructureTest(unittest.TestCase):
    def source(self):
        return (HERE / "controller_probes.py").read_text()

    def test_every_case_body_is_retried_only_through_bounded_attempts(self):
        source = self.source()
        # base_positive, arm_positive, the negatives loop and the late renewal.
        self.assertEqual(source.count("= bounded_attempts("), 4)
        self.assertNotIn("except RunnerDelay", source)
        self.assertIn('results["arm_renewal_delay"] = bounded_attempts("arm_renewal_delay", late_renewal, timing)',
                      source)

    def test_every_refusal_judgement_checks_motion_before_an_inconclusive_outcome(self):
        tree = ast.parse(self.source())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        def calls(node, names):
            return [call for call in ast.walk(node) if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name) and call.func.id in names]
        waits = calls(tree, ("wait_judged",))
        judged = [call for call in waits if flat(call.args[2].body).startswith("refusal(")]
        # The negatives (including the replay duplicate), the late renewal and the recovery.
        self.assertEqual(len(judged), 3)
        # The only other judgement wait is arm_hold's, which those motion checks call.
        self.assertEqual([call for call in waits if call not in judged],
                         calls(nested("arm_hold", tree=tree), ("wait_judged",)))
        # The refusal's timing checks after wait_judged can raise RunnerDelay as well (a refusal
        # near the lease or stale-permit end, a late-renewal hold at the goal lease end), so they
        # sit in the same block: the drift and hold checks still run first.
        timing = ("require_refused_inside_lease", "require_refused_inside_stale_window", "late_stop_verdict")
        later = {function: calls(nested(function, tree=tree), timing) for function in ("negative", "late_renewal")}
        self.assertEqual({function: sorted(call.func.id for call in found) for function, found in later.items()},
                         {"negative": ["require_refused_inside_lease", "require_refused_inside_stale_window"],
                          "late_renewal": ["late_stop_verdict", "require_refused_inside_lease",
                                           "require_refused_inside_stale_window"]})
        self.assertEqual(len(calls(tree, timing)), 5)
        for call in judged + later["negative"] + later["late_renewal"]:
            node = call
            while node in parents and not isinstance(node, ast.With):
                node = parents[node]
            self.assertIsInstance(node, ast.With, ast.unparse(call)[:80])
            self.assertEqual(ast.unparse(node.items[0].context_expr), "motion_checked(check_motion)",
                             ast.unparse(call)[:80])

    def test_positive_permits_leave_one_at_a_time(self):
        nodes = {node.name: node for node in ast.walk(ast.parse(self.source())) if isinstance(node, ast.FunctionDef)}
        for name, target in (("send_command", "base"), ("renew", "arm")):
            body = ast.unparse(nodes[name])
            check = "if chain.in_flight(guard('" + target + "').get('accepted')):\n"
            self.assertIn(check, body, name)
            self.assertLess(body.index(check), body.index("send_timely("), name)

    def test_precondition_miss_judges_every_row_since_the_reset(self):
        # The first `expired` row bounds a base lapse, so the rows must start at the reset,
        # before the packet build, not at the later pre-send wait.
        nodes = {node.name: node for node in ast.walk(ast.parse(self.source())) if isinstance(node, ast.FunctionDef)}
        body = ast.unparse(nodes["negative"])
        order = [body.index(text) for text in ("reset_grant = reset()[target]", "lease_mark = len(telemetry(target))",
                                               "packet = base_packet() if target == 'base' else arm_packet()[0]",
                                               "precondition_miss(target, stage, since(target, lease_mark) or [guard(target)]")]
        self.assertEqual(order, sorted(order))

    def test_positive_cases_judge_controller_events_before_a_retry_and_after_motion(self):
        tree = ast.parse(self.source())
        nodes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        def settled_first(node):
            while node in parents and not isinstance(node, ast.With):
                node = parents[node]
            return isinstance(node, ast.With) and flat(node.items[0].context_expr).startswith(
                "fail_closed_settled(lambda:settle(late,")
        def calls(function, name):
            return sorted((node for node in ast.walk(nodes[function]) if isinstance(node, ast.Call)
                           and isinstance(node.func, ast.Name) and node.func.id == name), key=lambda node: node.lineno)
        # Both positive controls keep their thresholds and wait through positive_motion.
        self.assertIn("positive_motion('base',chain,late,lambda:abs(world.pose()[0]-before[0])>0.03,4,",
                      flat(nodes["base_positive"]))
        self.assertIn("positive_motion('arm',chain,late,lambda:abs(world.primary_joint()-initial[0])>0.08,3,",
                      flat(nodes["arm_positive"]))
        # positive_motion: the action's RunnerDelay is settled first, then the last permit is
        # counted, the admission count must match the permits sent, and every event since the
        # checkpoint is judged.
        body = nodes["positive_motion"].body
        self.assertEqual(len(body), 4)
        self.assertEqual([settled_first(call) for call in calls("positive_motion", "wait_for")], [True, False])
        self.assertIn("action=action", flat(body[0]))
        self.assertIn("late()ornotchain.in_flight(guard(target).get('accepted'))", flat(body[1]))
        self.assertEqual(flat(body[2]), "chain.admitted(guard(target).get('accepted'),description)")
        self.assertEqual(flat(body[3]), "settle(late,description)")
        # The arm goal's approval and send checks are settled first as well.
        for function in ("arm_positive", "late_renewal"):
            goal_sends = [call for call in calls(function, "send_timely") if "goal" in ast.unparse(call)]
            self.assertEqual(len(calls(function, "arm_packet") + goal_sends), 2, function)
            self.assertTrue(all(map(settled_first, calls(function, "arm_packet") + goal_sends)), function)

    def test_refusal_judgements_keep_their_timing_and_hold_arguments(self):
        # Dropping the arm hold or widening the timing reasons would turn failures into retries.
        def judged(function):
            calls = [node for node in ast.walk(nested(function)) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "wait_judged"]
            self.assertEqual(len(calls), 1, function)
            return flat(calls[0].args[2].body)
        self.assertEqual(judged("negative"), "refusal(target,rows,negative_witness,NEGATIVE_REASONS[case],"
                                             "negative_timing(target,case),governing,stage,"
                                             "hold_afteriftarget=='arm'elseNone)")
        self.assertEqual(judged("late_renewal"), "refusal('arm',rows,negative_witness,NEGATIVE_REASONS['renewal_delay'],"
                                                 "negative_timing('arm','renewal_delay'),goal,stage,hold_after)")
        self.assertEqual(judged("recover"), "refusal(target,rows,witness,RECOVERY_REASONS[target],"
                                            "('freshness',)iftarget=='base'else(),None,stage+'recovery')")
        # ...and the values those names are bound to: governing = None would skip every lapse
        # before a refusal, and using the reset lease for the replay duplicate would turn a premature
        # lapse of the admitted first copy into runner delay. Tuple elements are compared, because
        # ast.unparse parenthesizes tuple targets on Python 3.9 only.
        def bindings(function, names):
            tree = nested(function)
            pairs = []
            for node in sorted((node for node in ast.walk(tree) if isinstance(node, ast.Assign)),
                               key=lambda node: node.lineno):
                for target in node.targets:
                    if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                        pairs += [(name.id, flat(value)) for name, value in zip(target.elts, node.value.elts)
                                  if isinstance(name, ast.Name) and name.id in names]
                    elif isinstance(target, ast.Name) and target.id in names:
                        pairs.append((target.id, flat(node.value)))
            # No other statement (augmented, annotated or walrus assignment, loop target) binds them.
            stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                      and isinstance(node.ctx, ast.Store) and node.id in names]
            self.assertEqual(len(pairs), len(stores), function)
            return pairs
        self.assertEqual(bindings("negative", ("governing", "hold_after", "first_grant")),
                         [("governing", "reset_grant['fields']"),
                          ("hold_after", "negative_witness['before_published_wall_ns']"),
                          ("first_grant", "permit_fields(data['permit'])"),
                          ("governing", "first_grant"), ("hold_after", "before['published_wall_ns']")])
        self.assertEqual(bindings("late_renewal", ("goal", "hold_after")),
                         [("goal", "chain.last()"), ("hold_after", "witnessed['published_wall_ns']")])
        # The base's 20 ms lease-end allowance applies to its own target.
        for function, call in (("negative", "require_refused_inside_lease(target,stage,rejection,"
                                            "negative_witness['lease_wall_end_ns'])"),
                               ("late_renewal", "require_refused_inside_lease('arm',stage,rejection,goal['wall_end_ns'])")):
            self.assertIn(call, flat(nested(function)), function)

    def test_motion_recovery_and_hold_failures_are_never_retried(self):
        # refused_motion() holds the only inconclusive motion branch (the arm replay's hold).
        for path in (("negative", "check_motion"), ("late_renewal", "check_motion"), ("recover",), ("arm_hold",)):
            names = {node.id for node in ast.walk(nested(*path)) if isinstance(node, ast.Name)}
            self.assertNotIn("RunnerDelay", names, path)
        self.assertIn("ifguard(target).get('accepted',0)!=witness['accepted_before']:raiseAssertionError('automaticrecovery')",
                      flat(nested("recover")))
        # Every arm negative needs its counted refusal and prompt hold; only the replay's hold
        # can make drift over the limit inconclusive.
        negative_motion = flat(nested("negative", "check_motion"))
        self.assertIn("iftarget=='arm':hold=arm_hold(stage,mark,negative_witness['sent_wall_ns'],"
                      "negative_witness['rejected_before'],hold_after)", negative_motion)
        self.assertIn("refused_motion(stage,drift,holdifcase=='replay'elseNone),hold", negative_motion)
        self.assertIn("refused_motion(stage,drift),arm_hold(stage,mark,sent,negative_witness['rejected_before'],"
                      "hold_after)", flat(nested("late_renewal", "check_motion")))
        # arm_hold gives the counted refusal and prompt hold the conclusive path's 2 s, then fails.
        hold = flat(nested("arm_hold"))
        self.assertIn("wait_judged('arm',mark,lambdarows:refusal_hold(rows,sent_ns,rejected_before,hold_after,"
                      "stage),2,", hold)
        self.assertRegex(hold, r"exceptTimeoutError:raiseAssertionError\(stage\+[\"']:nocontrollerrefusalandholdbound")

    def test_valid_arm_goal_waits_need_the_goal_counted_before_an_inconclusive_outcome(self):
        tree = ast.parse(self.source())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        def wrapper(node):
            while node in parents and not isinstance(node, ast.With):
                node = parents[node]
            return flat(node.items[0].context_expr) if isinstance(node, ast.With) else None
        def sites(function, kind, text):
            return [node for node in ast.walk(nested(function, tree=tree)) if isinstance(node, kind)
                    and text in ast.unparse(node)]
        # The goal's admission wait, the arm replay's first copy and the late-renewal goal,
        # including that goal's own "not seen moving" outcome. That outcome is raised before any
        # controller event made the attempt inconclusive, so the event counted afterwards is judged.
        late_goal = "goal_counted(lambda:(require_counted('arm',before,stage+'goal'),settle(late,stage+'goal')))"
        for function, kind, text, counted in (
                ("arm_positive", ast.Call, "'trusted controller admits signed arm goal'",
                 "goal_counted(lambda:require_counted('arm',before,'signedarmphysicallymoves'))"),
                ("negative", ast.Call, "' replay first packet actually admitted'",
                 "goal_counted(lambda:require_counted(target,witnessed,stage+'firstpacket'))"),
                ("late_renewal", ast.Call, "' arm moving under its live goal'", late_goal),
                ("late_renewal", ast.Raise, "goal_motion_not_witnessed_inside_goal_lease", late_goal)):
            found = sites(function, kind, text)
            self.assertEqual(len(found), 1, (function, text))
            self.assertEqual(wrapper(found[0]), counted, (function, text))
        # The counters are read before the goal leaves.
        for function in ("arm_positive", "late_renewal"):
            body = ast.unparse(nested(function, tree=tree))
            self.assertLess(body.index("before = guard('arm')"), body.index("send_timely("), function)
        # require_counted: arm goals only, the 1 s admission wait, then a failure, never a retry.
        counted = flat(nested("require_counted", tree=tree))
        self.assertIn("iftarget!='arm':return", counted)
        self.assertIn(",1,processes,stage+':validarmgoalcounted')exceptTimeoutError:raiseAssertionError(", counted)
        self.assertNotIn("RunnerDelay", counted)

    def test_harness_delays_before_a_refused_packet_leaves_judge_controller_events_first(self):
        tree = ast.parse(self.source())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        def settled_by(node):
            while node in parents and not isinstance(node, ast.With):
                node = parents[node]
            return flat(node.items[0].context_expr) if isinstance(node, ast.With) else None
        def presend(function, names):
            sites = [node for node in ast.walk(nested(function, tree=tree)) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id in names]
            self.assertEqual({node.func.id for node in sites}, set(names), function)
            return [(flat(node), settled_by(node)) for node in sites]
        names = ("base_packet", "arm_packet", "stale_permit", "require_timely", "require_fresh_witness",
                 "require_live", "precondition_miss")
        lease = "fail_closed_settled(lambda:settle(lease_events,stage+'resetlease'))"
        first = "fail_closed_settled(lambda:settle(first_late,stage+'firstpacket'))"
        for call, wrapper in presend("negative", names):
            self.assertEqual(wrapper, first if "duplicate" in call else lease, call)
        for call, wrapper in presend("late_renewal", ("arm_packet", "stale_permit", "require_fresh_witness",
                                                      "require_live")):
            self.assertEqual(wrapper, "fail_closed_settled(lambda:settle(late,stage+'goal'))", call)
        # Before the recovery packet leaves, the latch the refusal left is judged first.
        for call, wrapper in presend("recover", ("new_issuer", "approve", "base_packet", "arm_packet",
                                                 "require_timely")):
            self.assertEqual(wrapper, "fail_closed_settled(still_latched)", call)

    def test_deliberately_stale_permits_are_signed_at_send(self):
        source = self.source()
        self.assertEqual(len(re.findall(r"= stale_permit\(", source)), 2)
        self.assertNotIn("60_000_000, int(fields[6])", source)
        self.assertNotIn('goal["sim_end_ns"] - goal["sim_ns"])', source)


if __name__ == "__main__":
    unittest.main()
