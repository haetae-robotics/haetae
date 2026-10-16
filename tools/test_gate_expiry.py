"""Strict pre-fault expiry evidence, producer diagnostics and harness retry policy."""
import json
import unittest

import bench_evidence as evidence
from bench_gate import diagnostic_response
import test_bench_evidence as fixtures
import test_bench_attempts as harnesses


def cycles():
    status = {"mode": "normal", "armed": ["vla"], "recorder_ok": True,
              "state_ok": True, "arm_cancelling": False}
    motion = diagnostic_response({"stop": None, "cmd": {"linear": .25, "angular": 0},
        "outcome": {"decision": {"verdict": "yun", "expires_ms": 1200, "fired": []}},
        "status": dict(status, stop=None, active={}, active_expires_ms=1200)}, "motion", 1000, 1, 5, 6)
    world = diagnostic_response({"stop": "expired", "cmd": {"linear": 0, "angular": 0},
        "outcome": {"world_updated": {"stamp_ms": 1200}},
        "status": dict(status, stop="expired", active=None)}, "world", 1200, 1, 50, 60)
    return [{"result": "approved", "responses": [motion]}, {"result": "denied", "responses": [world]}]


def events(positive=True):
    rows = [{"device_ms": 100, "on": False, "reason": "armed", "renewed_ms": 100}]
    if positive:
        rows.append({"device_ms": 101, "on": True, "reason": "run", "renewed_ms": 100})
    return rows + [{"device_ms": 301, "on": False, "reason": "lease", "renewed_ms": 100},
                   {"device_ms": 302, "on": False, "reason": "stop", "renewed_ms": 100}]


def h2_logs(diagnostic=None, positive=True):
    relay = {"op": "RUN", "request_send_started_ns": 10, "request_sent_ns": 20,
             "response_first_byte_ns": 90, "response_received_ns": 100, "controller_stop_ack_ns": 110}
    auth = {"request_first_byte_ns": 30, "request_received_ns": 35, "gate_started_ns": 40,
            "gate_completed_ns": 70, "response_send_started_ns": 80, "response_sent_ns": 85}
    relay_log = ("허용: 보드가 서명을 확인함, LED ON\n" if positive else "") + \
        "차단: 서명 허가 중단, LED OFF\n" + json.dumps({"event": "permit_terminal", "reason": "gate_denied", "stages": [relay]}) + "\n"
    auth_log = "Authorizer terminal: gate_denied\n" + json.dumps({"event": "permit_gate_denial_diagnostic",
        "stages": [auth], "cycles": cycles() if diagnostic is None else diagnostic}) + "\n"
    return relay_log, auth_log


class GateExpiryTest(unittest.TestCase):
    def test_expiry_boundary_with_and_without_positive_control(self):
        for positive in (False, True):
            for request in (1200, 1201):
                diagnostic = cycles()
                diagnostic[-1]["responses"][0]["request_t_ms"] = request
                relay, auth = h2_logs(diagnostic, positive)
                result = evidence.h2_gate_expiry_loss(relay, 0, auth, events(positive))
                self.assertEqual((result["kind"], result["positive_control"]), ("gate-expiry", positive))
                host = fixtures.real_host_log(deny_cycle=2 if positive else 1, clean_return=True,
                                              denial_cycles=diagnostic)
                result = evidence.h1_gate_expiry_loss(host, 0, {"fault": None}, events(positive))
                self.assertEqual((result["kind"], result["positive_control"]), ("gate-expiry", positive))

    def test_other_denials_or_incomplete_state_never_qualify(self):
        changes = [("stop", "denied"), ("stop", "revoked"), ("status_stop", "denied"),
                   ("outcome", "rejected"), ("rejection_reason", "signature"),
                   ("command_zero", False), ("active_present", True), ("vla_armed", False),
                   ("recorder_ok", False), ("state_ok", False), ("arm_cancelling", True),
                   ("mode", "hold"), ("request_t_ms", 1199), ("request_t_ms", True),
                   ("request_started_ns", 0)]
        for key, value in changes:
            diagnostic = cycles()
            diagnostic[-1]["responses"][0][key] = value
            self.assertIsNone(evidence.gate_expiry_evidence(diagnostic, events()), (key, value))
        for diagnostic in ([], cycles()[-1:], cycles() + cycles(), [{}, {}]):
            self.assertIsNone(evidence.gate_expiry_evidence(diagnostic, events()))
        for key, value in (("decision_expires_ms", None), ("verdict", "bul"), ("active_expires_ms", 1199)):
            diagnostic = cycles()
            diagnostic[0]["responses"][-1][key] = value
            self.assertIsNone(evidence.gate_expiry_evidence(diagnostic, events()))

    def test_device_failure_or_reactivation_never_qualifies(self):
        for rows in ([], events()[:2], events() + [events()[1]],
                     [dict(row, reason="signature") if row["reason"] == "lease" else row for row in events()],
                     [dict(row, device_ms=299) if row["reason"] == "lease" else row for row in events()],
                     [dict(row, reason="stop", device_ms=401) if row["reason"] == "lease" else row for row in events()]):
            self.assertIsNone(evidence.gate_expiry_evidence(cycles(), rows))

    def test_completed_trace_fault_and_error_boundaries(self):
        relay, auth = h2_logs()
        for left, right, code in ((relay, auth, 1), (relay, auth + "Authorizer fault injected: person\n", 0),
                                 (relay, auth + fixtures.CRASH, 0), (relay + fixtures.CRASH, auth, 0),
                                 (relay, auth.replace("gate_denied", "operator_complete"), 0),
                                 (relay.splitlines()[0], auth, 0), (relay, auth.splitlines()[0], 0)):
            self.assertIsNone(evidence.h2_gate_expiry_loss(left, code, right, events()))
        for side, field, value in ((0, "controller_stop_ack_ns", None), (0, "op", "ARM"),
                                   (0, "controller_ack_ns", 95), (1, "sign_completed_ns", 75),
                                   (1, "gate_completed_ns", 49), (1, "request_first_byte_ns", 0)):
            logs = [relay.splitlines(), auth.splitlines()]
            row = json.loads(logs[side][-1]); row["stages"][-1][field] = value
            logs[side][-1] = json.dumps(row)
            self.assertIsNone(evidence.h2_gate_expiry_loss("\n".join(logs[0]), 0, "\n".join(logs[1]), events()), field)
        host = fixtures.real_host_log(deny_cycle=2, clean_return=True, denial_cycles=cycles())
        for ready in ({"fault": "person"}, {}, "unreadable"):
            self.assertIsNone(evidence.h1_gate_expiry_loss(host, 0, ready, events()))
        for bad in (host + "시험: fault\n", host + fixtures.CRASH, host + host, host.splitlines()[1]):
            self.assertIsNone(evidence.h1_gate_expiry_loss(bad, 0, {"fault": None}, events()))

    def test_real_relay_emits_bounded_terminal_trace_only_after_stop_ack(self):
        producer = fixtures.DriftGuardTest()
        log = producer.run_relay(["ON", "ON"], terminal_run=2, clean_return=True)
        terminal = evidence.json_rows(log.splitlines(), "permit_terminal")[0]
        self.assertEqual(terminal["reason"], "gate_denied")
        self.assertEqual([stage["op"] for stage in terminal["stages"]], ["BIND", "ARM", "RUN", "RUN"])
        self.assertIn("controller_stop_ack_ns", terminal["stages"][-1])
        self.assertNotIn("controller_ack_ns", terminal["stages"][-1])
        failed = producer.run_relay(["ON", "ON"], terminal_run=2, stop_error="H2 USB response timeout")
        self.assertFalse(evidence.json_rows(failed.splitlines(), "permit_terminal"))

    def test_harnesses_retry_proven_expiry_but_never_count_it_as_pass(self):
        relay, auth = h2_logs()
        host = fixtures.real_host_log(deny_cycle=2, clean_return=True, denial_cycles=cycles())
        for positive in (False, True):
            result, calls, attempts = harnesses.PermitHarnessTest().run_attempt(events(positive), relay, 0,
                auth_text=auth, reaches_on=positive, via_case=True)
            self.assertIsInstance(result, AssertionError)
            self.assertIn("inconclusive never counts as a pass", str(result))
            self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"])
            log = host if positive else fixtures.real_host_log(deny_cycle=1, clean_return=True, denial_cycles=cycles())
            result, calls, attempts = harnesses.BenchHarnessTest().run_attempt(events(positive), log,
                {"gate_pid": 1, "fault": None}, 0, reaches_on=positive, scenario="person", via_case=True)
            self.assertIsInstance(result, AssertionError)
            self.assertIn("inconclusive never counts as a pass", str(result))
            self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"])
        for auth_exit in (None, -9):
            result, _, attempts = harnesses.PermitHarnessTest().run_attempt(events(), relay, 0, auth_text=auth,
                auth_exit=auth_exit, via_case=True)
            self.assertNotIn("inconclusive", str(result))
            self.assertEqual(attempts, ["case"])
        # Once a pause or kill happened, this evidence cannot trigger retries.
        for flags in ({"pause": True}, {"kill_authorizer": True}):
            result, _, attempts = harnesses.PermitHarnessTest().run_attempt(events(), relay, 0, auth_text=auth,
                via_case=True, scenario="allow", **flags)
            self.assertIsInstance(result, AssertionError)
            self.assertEqual(attempts, ["case"])


if __name__ == "__main__":
    unittest.main()
