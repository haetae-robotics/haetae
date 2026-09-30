"""World revocation must advance a scene even without a proposal Decision."""

import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
