"""Fault rejection and terminal delivery are separate board-free observations."""
import json
import unittest

import bench_gate
import test_bench_attempts as attempts
from bench_evidence import h2_rejected_fault_stop, h2_timing_loss


# Reduced stages/device rows from main CI 38612027337, macOS H2 replay.
# That old artifact has no exact rejection/zero-command projection: it is NOT
# retrospectively accepted. The new fields below come from the raw Rust response
# shape and the current diagnostic_response(), to test future evidence end to end.
RELAY_STAGE = {"op": "RUN", "started_ns": 176727981041,
               "request_send_started_ns": 176728009500, "request_sent_ns": 176728040083}
AUTH_STAGE = {"request_first_byte_ns": 176728173833, "request_received_ns": 176728180666,
              "gate_started_ns": 176728263083, "gate_completed_ns": 176780164750,
              "response_send_started_ns": 176781630416, "response_sent_ns": 176781672416}
EVENTS = [
    {"device_ms": 804, "on": False, "reason": "armed", "sequence": 2, "renewed_ms": 798},
    {"device_ms": 836, "on": True, "reason": "run", "sequence": 3, "renewed_ms": 831},
    {"device_ms": 1788, "on": True, "reason": "run", "sequence": 16, "renewed_ms": 1780},
    {"device_ms": 1942, "on": False, "reason": "stop", "sequence": 16, "renewed_ms": 1780}]


def fixture(scenario="replay"):
    world = {"phase": "world", "outcome": "world_updated", "stop": None,
             "request_started_ns": 176728377208, "response_received_ns": 176767704750}
    raw = {"cmd": {"linear": 0, "angular": 0}, "stop": "denied",
           "outcome": {"rejected": {"error": "replayed or zero counter" if scenario == "replay"
                                     else "input signature failed"}},
           "status": {"mode": "normal", "stop": "denied", "armed": [], "active": None,
                      "recorder_ok": True, "state_ok": True, "arm_cancelling": False}}
    motion = bench_gate.diagnostic_response(raw, "motion", 1792001155241, 47.063,
                                            176768708208, 176775327416)
    denial = {"event": "permit_gate_denial_diagnostic", "cycles": [
        {"result": "denied", "responses": [world, motion]}], "stages": [dict(AUTH_STAGE)]}
    relay = {"event": "permit_ipc_failure", "stages": [dict(RELAY_STAGE)]}
    return relay, denial


def logs(relay, denial, scenario="replay"):
    return ("허용: 보드가 서명을 확인함, LED ON\n" + json.dumps(relay) + "\npermit 오류: timed out\n",
            "Authorizer fault injected: " + scenario + "\nAuthorizer terminal: gate_denied\n"
            + json.dumps(denial) + "\n")


class RejectionDeliveryTest(unittest.TestCase):
    def test_verified_rejection_with_late_terminal_is_conclusive_and_never_retried(self):
        harness = attempts.PermitHarnessTest()
        for scenario, reason in (("replay", "replay"), ("invalid-signature", "signature")):
            with self.subTest(scenario=scenario):
                relay, auth = logs(*fixture(scenario), scenario)
                result, _, tried = harness.run_attempt(EVENTS, relay, 1, late_auth=auth,
                                                       scenario=scenario, via_case=True)
                self.assertIsInstance(result, dict, result)
                self.assertEqual(tried, ["case"])
                self.assertEqual((result["passed"], result["positive_control"], result["relay_exit"],
                                  result["last_run_to_off_ms"]), (True, True, 1, 162))
                delivery = result["denial_delivery"]
                self.assertEqual((delivery["rejection_reason"], delivery["blocking"],
                                  delivery["terminal_delivery"]), (reason, False, "deadline-missed"))
                self.assertAlmostEqual(delivery["request_to_terminal_sent_ms"], 53.691, places=3)
                self.assertIsNone(h2_timing_loss(relay, 1, auth, EVENTS))  # never an inconclusive retry

    def test_missing_or_wrong_rejection_never_passes_or_retries(self):
        harness = attempts.PermitHarnessTest()
        for reason in (None, "signature", "unknown", ["replay"]):
            relay, denial = fixture()
            denial["cycles"][-1]["responses"][-1]["rejection_reason"] = reason
            relay_log, auth_log = logs(relay, denial)
            result, _, tried = harness.run_attempt(EVENTS, relay_log, 1, late_auth=auth_log,
                                                   scenario="replay", via_case=True)
            self.assertIsInstance(result, AssertionError, reason)
            self.assertEqual(tried, ["case"])

    def test_old_diagnostic_or_incomplete_authorizer_is_not_reinterpreted_as_a_pass(self):
        relay, denial = fixture()
        for field in ("rejection_reason", "command_zero"):
            del denial["cycles"][-1]["responses"][-1][field]
        relay_log, auth_log = logs(relay, denial)
        self.assertIsNone(h2_rejected_fault_stop(relay_log, 1, auth_log, EVENTS, "replay"))
        relay_log, auth_log = logs(*fixture())
        for exit_code in (None, -9):
            result, _, tried = attempts.PermitHarnessTest().run_attempt(
                EVENTS, relay_log, 1, auth_text=auth_log, auth_exit=exit_code,
                scenario="replay", via_case=True)
            self.assertIsInstance(result, AssertionError)
            self.assertEqual(tried, ["case"])

    def test_corrupt_or_mismatched_witnesses_fail_closed(self):
        # These are content/completeness failures, irrespective of measured timing.
        mutations = [
            lambda r, d: r["stages"][-1].update(op="ARM"),
            lambda r, d: r["stages"][-1].update(response_received_ns=176779000000),
            lambda r, d: r["stages"][-1].update(controller_ack_ns=176779000000),
            lambda r, d: r["stages"][-1].update(request_sent_ns=176800000000),
            lambda r, d: d["stages"][-1].update(sign_completed_ns=176780200000),
            lambda r, d: d["stages"][-1].pop("response_sent_ns"),
            lambda r, d: d["cycles"][-1].update(result="approved"),
            lambda r, d: d["cycles"][-1]["responses"].pop(),
            lambda r, d: d["cycles"][-1]["responses"][0].update(stop="expired"),
            lambda r, d: d["cycles"][-1]["responses"][-1].update(command_zero=False),
            lambda r, d: d["cycles"][-1]["responses"][-1].update(vla_armed=True),
            lambda r, d: d["cycles"][-1]["responses"][-1].update(active_present=True),
            lambda r, d: d["cycles"][-1]["responses"][-1].update(recorder_ok=False),
            lambda r, d: d["cycles"][-1]["responses"][-1].update(response_received_ns=None),
        ]
        for index, change in enumerate(mutations):
            relay, denial = fixture()
            change(relay, denial)
            relay_log, auth_log = logs(relay, denial)
            self.assertIsNone(h2_rejected_fault_stop(relay_log, 1, auth_log, EVENTS, "replay"), index)
        relay, denial = fixture()
        for key in ("cycles", "stages"):
            for value in (None, [], "bad", [None]):
                bad = dict(denial, **{key: value})
                relay_log, auth_log = logs(relay, bad)
                self.assertIsNone(h2_rejected_fault_stop(relay_log, 1, auth_log, EVENTS, "replay"))

    def test_no_positive_late_off_or_on_after_off_cannot_qualify(self):
        relay, auth = logs(*fixture())
        for events in (EVENTS[:1] + EVENTS[-1:], EVENTS[:-1],
                       EVENTS + [dict(EVENTS[-1], on=True, reason="run")],
                       EVENTS[:-1] + [dict(EVENTS[-1], device_ms=2081)],
                       EVENTS[:-1] + [dict(EVENTS[-1], device_ms=1979, reason="lease")]):
            self.assertIsNone(h2_rejected_fault_stop(relay, 1, auth, events, "replay"))
        # Control: the 300 ms stop observation bound is unchanged and inclusive.
        self.assertIsNotNone(h2_rejected_fault_stop(relay, 1, auth,
                             EVENTS[:-1] + [dict(EVENTS[-1], device_ms=2080)], "replay"))

    def test_other_terminals_content_errors_crashes_and_interruptions_stay_failures(self):
        relay, auth = logs(*fixture())
        for changed_relay, changed_auth, scenario in (
                (relay, auth.replace("gate_denied", "authorizer_failure"), "replay"),
                (relay, auth.replace("Authorizer fault injected: replay\n", ""), "replay"),
                (relay, auth + "Traceback (most recent call last):\nKeyError: crash\n", "replay"),
                (relay.replace("timed out", "invalid H2 controller response: signature"), auth, "replay"),
                (relay, auth, "allow"), (relay, auth, "person"), (relay, auth, "world-loss")):
            self.assertIsNone(h2_rejected_fault_stop(changed_relay, 1, changed_auth, EVENTS, scenario))
        for flags in ({"pause": True}, {"kill_authorizer": True}):
            result, _, _ = attempts.PermitHarnessTest().run_attempt(
                EVENTS, relay, 1, late_auth=auth, scenario="replay", **flags)
            self.assertIsInstance(result, dict)
            self.assertNotIn("denial_delivery", result)  # judged only by the existing interruption path


if __name__ == "__main__":
    unittest.main()
