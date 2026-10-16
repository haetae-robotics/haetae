"""Gate diagnostics use only bounded projections and never make authorization decisions."""
import contextlib
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bench_gate


def step(phase, allowed=True, stop=None, expires=1200):
    return {"cmd": {"linear": .25 if allowed and phase == "motion" else 0, "angular": 0},
            "stop": stop,
            "outcome": {"world_updated": {"stamp_ms": 1000}} if phase == "world" else {
                "decision": {"verdict": "yun" if allowed else "bul", "fired": [], "expires_ms": expires}},
            "status": {"mode": "normal", "stop": stop, "world_age_ms": 0, "active_expires_ms": expires,
                       "armed": ["vla"], "active": {"source": "vla"} if allowed else None,
                       "recorder_ok": True, "state_ok": True, "arm_cancelling": False}}


def fake_gate(responses):
    gate = bench_gate.BenchGate.__new__(bench_gate.BenchGate)
    gate.last_approved_cycle = gate.last_cycle = gate._diagnostic_cycle = None
    gate.proposal = 0
    gate.last_motion = None
    gate.signer = SimpleNamespace(sign=lambda role, value: {"role": role, "payload": value})
    requests = []
    replies = iter(responses)
    def request(message):
        requests.append(copy.deepcopy(message))
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    gate.bridge = SimpleNamespace(request=request)
    return gate, requests


class GateDiagnosticTest(unittest.TestCase):
    def test_motion_fault_marker_is_absent_when_world_preparation_fails(self):
        for fault in ("replay", "invalid-signature"):
            for response in (bench_gate.BridgeFailure("gate response timeout"),
                             step("world", stop="expired", expires=None)):
                with self.subTest(fault=fault, response=response):
                    gate, requests = fake_gate([response])
                    injected = Mock()
                    with patch.object(bench_gate, "now_ms", return_value=1000):
                        if isinstance(response, Exception):
                            with self.assertRaises(bench_gate.BridgeFailure):
                                gate.cycle(fault, on_fault=injected)
                        else:
                            self.assertFalse(gate.cycle(fault, on_fault=injected))
                    injected.assert_not_called()
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(json.loads(requests[0]["data"])["role"], "world")

    def test_fault_marker_precedes_the_fault_request_even_when_it_times_out(self):
        for fault in ("person", "world-loss", "replay", "invalid-signature"):
            with self.subTest(fault=fault):
                responses = ([step("world")] if fault in ("replay", "invalid-signature") else [])
                gate, requests = fake_gate(responses + [bench_gate.BridgeFailure("gate response timeout")])
                gate.last_motion = {"role": "vla", "payload": {"id": 17}}
                observed = []
                with patch.object(bench_gate, "now_ms", return_value=1000):
                    with self.assertRaises(bench_gate.BridgeFailure):
                        gate.cycle(fault, on_fault=lambda value: observed.append((value, len(requests))))
                expected_before = 1 if fault in ("replay", "invalid-signature") else 0
                self.assertEqual(observed, [(fault, expected_before)])
                self.assertEqual(len(requests), expected_before + 1)
                sent = json.loads(requests[-1]["data"])
                if fault == "person":
                    self.assertEqual(sent["payload"]["humans"][0]["id"], "bench-person")
                elif fault == "replay":
                    self.assertEqual(sent, gate.last_motion)
                elif fault == "invalid-signature":
                    self.assertEqual(sent["signature"], "00" * 64)
                else:
                    self.assertEqual(sent["role"], "vla")

    def test_fault_observer_time_cannot_extend_positive_response_freshness(self):
        gate, _ = fake_gate([step("world"), step("motion")])
        clock = [0.0]
        def slow_observer(_):
            clock[0] = .060
        with patch.object(bench_gate, "now_ms", return_value=1000), \
                patch.object(bench_gate.time, "monotonic", lambda: clock[0]):
            with self.assertRaisesRegex(bench_gate.BridgeFailure, "elapsed 60.0 ms >= 50 ms"):
                gate.cycle("invalid-signature", on_fault=slow_observer)
        self.assertIsNone(gate.approval)
        self.assertIsNone(gate.last_approved_cycle)

    def test_early_world_denial_preserves_previous_approved_expiry_and_exact_request_t(self):
        gate, requests = fake_gate([step("world"), step("motion"), step("world", stop="expired", expires=None)])
        with patch.object(bench_gate, "now_ms", side_effect=range(1000, 1100)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(gate.cycle())
            self.assertFalse(gate.cycle())
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(requests), 3)  # the world denial never reaches a motion request
        approved, denied = gate.denial_diagnostics()
        self.assertEqual((approved["result"], denied["result"]), ("approved", "denied"))
        self.assertEqual([row["phase"] for row in approved["responses"]], ["world", "motion"])
        self.assertEqual(approved["responses"][-1]["active_expires_ms"], 1200)
        self.assertEqual(denied["responses"][0]["stop"], "expired")
        self.assertIsNone(denied["responses"][0]["active_expires_ms"])
        actual_t = [request["t"] for request in requests]
        recorded_t = [row["request_t_ms"] for cycle in (approved, denied) for row in cycle["responses"]]
        self.assertEqual(recorded_t, actual_t)
        stamps = [row[key] for cycle in (approved, denied) for row in cycle["responses"]
                  for key in ("request_started_ns", "response_received_ns")]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(type(value) is int for value in stamps))
        self.assertEqual(approved["responses"][-1]["vla_armed"], True)
        self.assertEqual(approved["responses"][-1]["active_present"], True)
        self.assertIsNone(gate.approval)

    def test_motion_denial_and_new_cycle_do_not_reuse_stale_responses(self):
        gate, _ = fake_gate([step("world"), step("motion", allowed=False, stop="denied", expires=None),
                            step("motion", allowed=False, stop="stale_world", expires=None), RuntimeError("fail")])
        with patch.object(bench_gate, "now_ms", return_value=1000):
            self.assertFalse(gate.cycle())
            first = copy.deepcopy(gate.denial_diagnostics())
            self.assertFalse(gate.cycle("world-loss"))
            second = gate.denial_diagnostics()
            self.assertEqual(len(first[0]["responses"]), 2)
            self.assertEqual([row["phase"] for row in second[0]["responses"]], ["motion"])
            self.assertEqual(second[0]["responses"][0]["stop"], "stale_world")
            with self.assertRaisesRegex(RuntimeError, "fail"):
                gate.cycle()
        self.assertEqual(gate.denial_diagnostics(), [{"result": "incomplete", "responses": []}])
        self.assertIsNone(gate._diagnostic_cycle)
        self.assertIsNone(gate.approval)

    def test_world_rejection_stops_before_motion_without_retaining_error_text(self):
        response = step("world")
        response["outcome"] = {"rejected": {"error": "arbitrary private payload"}}
        gate, requests = fake_gate([response])
        with patch.object(bench_gate, "now_ms", return_value=1000):
            self.assertFalse(gate.cycle())
        self.assertEqual(len(requests), 1)
        diagnostic = gate.denial_diagnostics()
        self.assertEqual(diagnostic[0]["responses"][0]["outcome"], "rejected")
        self.assertNotIn("arbitrary private payload", json.dumps(diagnostic))

    def test_repeated_positive_cycles_keep_only_the_latest_approval_and_never_write_output(self):
        gate, _ = fake_gate([step(phase, expires=1200 + index * 10) for index in range(10) for phase in ("world", "motion")]
                            + [step("world", stop="expired", expires=None)])
        with patch.object(bench_gate, "now_ms", return_value=1000), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            for _ in range(10):
                self.assertTrue(gate.cycle())
                self.assertEqual(len(gate.denial_diagnostics()), 1)
                self.assertIsNotNone(gate.approval)
            self.assertFalse(gate.cycle())
        self.assertEqual(output.getvalue(), "")
        self.assertEqual([len(cycle["responses"]) for cycle in gate.denial_diagnostics()], [2, 1])
        self.assertEqual(gate.denial_diagnostics()[0]["responses"][-1]["active_expires_ms"], 1290)

    def test_projection_never_copies_arbitrary_strings_or_unbounded_values(self):
        secret = "private-value-" * 1000
        bad = {"cmd": {"linear": secret}, "stop": {"mode": secret}, "payload": secret,
               "outcome": {"rejected": {"error": secret}},
               "status": {"mode": secret, "stop": secret, "world_age_ms": 2 ** 65,
                          "active_expires_ms": -1, "recorder_ok": secret, "state_ok": 1,
                          "arm_cancelling": secret, "active": {"signature": secret}}}
        projected = bench_gate.diagnostic_response(bad, secret, True, float("inf"))
        self.assertNotIn("private-value", json.dumps(projected, allow_nan=False))
        self.assertEqual(projected["outcome"], "rejected")
        self.assertIsNone(projected["rejection_reason"])
        self.assertFalse(projected["command_zero"])
        for key in ("phase", "stop", "request_t_ms", "elapsed_ms", "mode", "world_age_ms", "active_expires_ms",
                    "recorder_ok", "state_ok", "arm_cancelling"):
            self.assertIsNone(projected[key], key)
        bad["outcome"] = {"decision": {"verdict": secret, "fired": [secret] * 70000}}
        projected = bench_gate.diagnostic_response(bad, "motion", 1000, 1.234567)
        self.assertEqual((projected["fired_count"], projected["elapsed_ms"]), (65535, 1.235))
        self.assertIsNone(projected["verdict"])
        self.assertLess(len(json.dumps(projected, allow_nan=False)), 900)
        for value in (True, -1, float("nan"), float("inf"), 10 ** 1000):
            self.assertIsNone(bench_gate.diagnostic_uint(value))
            self.assertIsNone(bench_gate.diagnostic_elapsed(value))
        for value in ({"mode": "hold"}, {"mode": "hold", "secret": secret}, [secret]):
            expected = "mode:hold" if value == {"mode": "hold"} else None
            self.assertEqual(bench_gate.diagnostic_stop(value), expected)

    def test_rejection_projection_requires_exact_error_and_numeric_zero_command(self):
        for error, reason in (("replayed or zero counter", "replay"), ("input signature failed", "signature"),
                              ("replayed or zero counter: private payload", None), (["input signature failed"], None)):
            response = step("motion", allowed=False, stop="denied", expires=None)
            response["outcome"] = {"rejected": {"error": error}}
            value = bench_gate.diagnostic_response(response, "motion", 1000, 1)
            self.assertEqual(value["rejection_reason"], reason)
            self.assertTrue(value["command_zero"])
            for cmd in ({"linear": True, "angular": 0}, {"linear": 0},
                        {"linear": .25, "angular": 0}, {"linear": "0", "angular": 0}):
                response["cmd"] = cmd
                self.assertFalse(bench_gate.diagnostic_response(response, "motion", 1000, 1)["command_zero"])


if __name__ == "__main__":
    unittest.main()
