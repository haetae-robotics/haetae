"""World revocation must advance a scene even without a proposal Decision."""

import json
from pathlib import Path
import tempfile
import unittest
from stop_evidence import world_expiry_stop_observed, sensor_stop_report

from stop_evidence import person_stop_observed, person_stop_report


class StopEvidenceTest(unittest.TestCase):
    def test_revocation_matches_causal_world_and_subsequent_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sillok.jsonl"
            rows = [
                {"kind": "world", "payload": {"stamp_ms": 1000}},
                {"kind": "rejudge", "payload": {"fired": []}},
                {"kind": "world", "payload": {"stamp_ms": 1050}},
                {"kind": "revoke", "payload": {"fired": ["person"]}},
                {"kind": "stop", "payload": {"reason": "Revoked"}},
            ]
            log.write_text("\n".join(json.dumps(row) for row in rows) + '\n{"kind":')
            reports = [{"stamp_ms": 1000, "wall": 10.0}, {"stamp_ms": 1050, "wall": 10.05}]
            self.assertEqual(person_stop_report(log, reports, 1000), reports[1])
            self.assertFalse(person_stop_observed(log, reports, [(10.01, 1001)], 1000))
            self.assertTrue(person_stop_observed(log, reports, [(10.06, 1060)], 1000))
            self.assertFalse(person_stop_observed(log, reports, [(10.06, 1060)], 1100))
            self.assertFalse(person_stop_observed(log, reports[:1], [(10.06, 1060)], 1000))

    def test_zero_or_unrelated_stop_is_insufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sillok.jsonl"
            log.write_text(json.dumps({"kind": "world", "payload": {"stamp_ms": 1000}}) + "\n" +
                           json.dumps({"kind": "stop", "payload": {"reason": "Unarmed"}}))
            self.assertFalse(person_stop_observed(log, [{"stamp_ms": 1000, "wall": 10}],
                                                  [(11, 1100)], 1000))


class WorldExpiryTest(unittest.TestCase):
    def test_expiry_requires_current_disarmed_state_and_specific_world_error(self):
        outcome = {"rejected": {"error": "stale actuation response: world expired"}}
        states = [(10, {"stop": "denied", "armed": []})]
        self.assertEqual(world_expiry_stop_observed(states, [(10, outcome)], 10), "world_expired_at_response")
        self.assertIsNone(world_expiry_stop_observed(states, [(9, outcome)], 10))
        self.assertIsNone(world_expiry_stop_observed([(10, {"stop": "denied", "armed": ["vla"]})], [(10, outcome)], 10))
        self.assertIsNone(world_expiry_stop_observed(states, [(10, {"rejected": {"error": "proposal expired"}})], 10))


class SensorStopTest(unittest.TestCase):
    def check(self, rows, states=None, zeros=None, outcomes=None, require_perception=True):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sillok.jsonl"
            log.write_text("\n".join(json.dumps(row) for row in rows) + '\n{"kind":')
            return sensor_stop_report(
                log, states if states is not None else [(10.06, {"stop": "revoked", "armed": []})],
                outcomes or [], zeros if zeros is not None else [(10.05, 1050)],
                10, 1000, require_perception=require_perception)

    def rows(self, confidence=0, stamp=1020, received=1030, fired="perception-unknown", kind="revoke"):
        return [{"kind": "world", "ts_ms": received,
                 "payload": {"stamp_ms": stamp, "confidence": confidence}},
                {"kind": kind, "ts_ms": 1040, "payload": {"fired": [fired]}}]

    def test_requires_causal_unknown_world_revocation_disarm_and_subsequent_zero(self):
        report = self.check(self.rows())
        self.assertEqual(report, {"stop_reason": "perception_unknown", "zero_at": 10.05,
                                  "world_stamp_ms": 1020})
        for rows in (self.rows(confidence=1), self.rows(received=999),
                     self.rows(fired="person"), self.rows(kind="decision"),
                     self.rows()[1:], self.rows()[:1]):
            with self.subTest(rows=rows):
                self.assertIsNone(self.check(rows))
        for states in ([], [(9, {"stop": "revoked", "armed": []})],
                       [(10.06, {"stop": "revoked", "armed": ["vla"]})],
                       [(10.06, {"stop": "unarmed", "armed": []})]):
            with self.subTest(states=states):
                self.assertIsNone(self.check(self.rows(), states=states))
        self.assertIsNone(self.check(self.rows(), zeros=[(10.01, 1030)]))
        self.assertIsNone(self.check(self.rows(), zeros=[(9.9, 1050)]))
        # The world source stamp is mechanical feedback, which can precede
        # the fault while still fresh. Correlate the engine's accepted-world
        # receipt time, preserving that original source timestamp.
        report = self.check(self.rows(stamp=995))
        assert report is not None
        self.assertEqual(report["world_stamp_ms"], 995)

    def test_latest_world_must_be_unknown(self):
        rows = self.rows()
        rows.insert(1, {"kind": "world", "ts_ms": 1035,
                        "payload": {"stamp_ms": 1035, "confidence": 1}})
        self.assertIsNone(self.check(rows))

    def test_expiry_remains_available_only_for_disappearing_observer(self):
        states = [(10.05, {"stop": "stale_world", "armed": []})]
        self.assertIsNone(self.check([], states=states))
        self.assertEqual(self.check([], states=states, require_perception=False),
                         {"stop_reason": "stale_world", "zero_at": 10.05})
        self.assertIsNone(self.check([], states=states, zeros=[], require_perception=False))

    def test_dual_clock_world_expiry_needs_engine_disarm_and_named_rejection(self):
        states = [(10.05, {"stop": "denied", "armed": []})]
        outcomes = [(10.06, {"rejected": {"error":
            "trusted world did not advance within dual-clock freshness"}})]
        self.assertEqual(self.check([], states=states, outcomes=outcomes, require_perception=False),
                         {"stop_reason": "world_expired_at_owner", "zero_at": 10.05})
        # Coverage loss still needs the signed unknown-perception revocation.
        self.assertIsNone(self.check([], states=states, outcomes=outcomes))
        self.assertIsNone(self.check([], states=states, outcomes=[], require_perception=False))
        self.assertIsNone(self.check([], states=states, outcomes=outcomes, zeros=[], require_perception=False))
        self.assertIsNone(self.check([], states=[(10.05, {"stop": "denied", "armed": ["vla"]})],
                                     outcomes=outcomes, require_perception=False))


if __name__ == "__main__":
    unittest.main()
