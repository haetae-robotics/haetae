"""Only the dispatched immutable household proposal can explain its denial."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from hazard_dispatch_evidence import (MAX_DISPATCH_LOG_BYTES, MAX_DISPATCH_LOG_RECORDS,
                                      dispatch_denial)


class DispatchDenialTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "sillok.jsonl"
        self.points = [(0, [0, 0.0]), (1000, [0.375, 0.0])]
        self.binding = {"schema_version": 1, "world_revision": 12, "task_revision": 4,
                        "task_id": "household-lab-chemicals", "step_id": "fixture-motion-4",
                        "robot_id": "reference", "model_sha256": "a" * 64,
                        "tool_id": "test-tool", "item_id": "inert"}
        self.proposal = {"id": 53, "source": "vla", "timestamp_ms": 101,
                         "semantic": self.binding,
                         "action": {"type": "joint_trajectory", "ttl_ms": 1000,
                                    "points": [{"time_from_start_ms": ms, "positions": list(values)}
                                               for ms, values in self.points]}}
        self.decision = {"proposal_id": 53, "verdict": "bul", "action": None,
                         "fired": ["household:missing-world"], "mode": "normal", "speed_cap": None}

    def pair(self, proposal=None, decision=None):
        return [("proposal", {"proposal": proposal or self.proposal, "world": None}, 102),
                ("decision", decision or self.decision, 102)]

    def write(self, records):
        previous = "0" * 64
        rows = []
        for seq, (kind, payload, stamp) in enumerate(records):
            digest = f"{seq + 1:064x}"
            rows.append({"seq": seq, "ts_ms": stamp, "kind": kind, "payload": payload,
                         "prev": previous, "hash": digest})
            previous = digest
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def result(self):
        return dispatch_denial(self.path, root=self.root, expected_uid=os.geteuid(),
                               points=self.points, binding=self.binding, sent_sim_ms=100)

    def assert_unknown(self, reason=None):
        result = self.result()
        self.assertEqual(result["correlation"], "unknown")
        self.assertEqual(set(result), {"correlation", "reason"})
        if reason:
            self.assertEqual(result["reason"], reason)

    def test_exact_denial_uses_full_proposal_even_when_decision_action_is_null(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["action"]["points"][0]["positions"][0] = 0.0
        self.write(self.pair(proposal=proposal))
        self.assertEqual(self.result(), {"correlation": "exact", "reason": "matching_proposal_denied",
                         "proposal_id": 53, "fired": ["household:missing-world"],
                         "proposal_seq": 0, "decision_seq": 1, "ts_ms": 102,
                         "proposal_timestamp_ms": 101})

    def test_valid_automatic_seal_can_separate_proposal_and_decision(self):
        records = self.pair()
        records.insert(1, ("seal", {"sig": "a" * 128, "key_id": "b" * 16}, 1_000_000))
        self.write(records)
        self.assertEqual(self.result()["decision_seq"], 2)
        records[1] = ("seal", {"sig": "bad", "key_id": "b" * 16}, 1_000_000)
        self.write(records)
        self.assert_unknown("malformed_log")

    def test_both_source_and_engine_timestamps_must_follow_dispatch(self):
        for source_stamp, engine_stamp in ((99, 102), (101, 99)):
            with self.subTest(source=source_stamp, engine=engine_stamp):
                proposal = copy.deepcopy(self.proposal)
                proposal["timestamp_ms"] = source_stamp
                self.write([(kind, payload, engine_stamp) for kind, payload, _ in self.pair(proposal)])
                self.assert_unknown("no_matching_proposal")

    def test_other_source_binding_trajectory_and_lease_do_not_match(self):
        for field in ("source", "binding", "positions", "time", "ttl", "numeric_bool"):
            with self.subTest(field=field):
                proposal = copy.deepcopy(self.proposal)
                if field == "source":
                    proposal["source"] = "teleop"
                elif field == "binding":
                    proposal["semantic"]["step_id"] = "fixture-motion-3"
                elif field == "positions":
                    proposal["action"]["points"][-1]["positions"][0] += 0.001
                elif field == "time":
                    proposal["action"]["points"][-1]["time_from_start_ms"] = 999
                elif field == "ttl":
                    proposal["action"]["ttl_ms"] = 999
                else:
                    proposal["action"]["points"][0]["time_from_start_ms"] = False
                self.write(self.pair(proposal))
                self.assert_unknown("no_matching_proposal")

    def test_multiple_matching_proposals_are_ambiguous_even_with_same_id(self):
        self.write(self.pair() + self.pair())
        self.assert_unknown("ambiguous_proposal")

    def test_decision_must_be_next_nonseal_record_with_same_id_and_time(self):
        for variation in ("other_id", "other_time", "intervening_world", "rejected"):
            with self.subTest(variation=variation):
                records = self.pair(decision=copy.deepcopy(self.decision))
                if variation == "other_id":
                    records[1][1]["proposal_id"] = 54
                elif variation == "other_time":
                    records[1] = ("decision", self.decision, 103)
                elif variation == "intervening_world":
                    records.insert(1, ("world", {"stamp_ms": 102}, 102))
                else:
                    records[1] = ("reject", {"error": "history:consumed-step", "input": ""}, 102)
                self.write(records)
                self.assert_unknown("mismatched_decision")

    def test_accepted_proposal_and_anonymous_rejection_do_not_establish_denial(self):
        decision = {**self.decision, "verdict": "yun", "action": self.proposal["action"]}
        self.write(self.pair(decision=decision) + [("reject", {"error": "failure", "input": ""}, 103)])
        self.assert_unknown("no_matching_denial")

    def test_absent_empty_partial_and_missing_decision_remain_unknown(self):
        self.assert_unknown("log_unavailable")
        self.path.write_text("")
        self.assert_unknown("no_matching_proposal")
        self.write(self.pair()[:1])
        self.assert_unknown("missing_decision")
        self.write(self.pair())
        self.path.write_text(self.path.read_text() + '{"seq":')
        self.assert_unknown("partial_log")

    def test_diagnostic_byte_limit_rejects_oversized_file_before_reading_it(self):
        self.write(self.pair())
        with self.path.open("r+b") as stream:
            stream.truncate(MAX_DISPATCH_LOG_BYTES + 1)
        self.assert_unknown("log_unavailable")

    def test_diagnostic_record_limit_precedes_json_parsing(self):
        self.path.write_text("\n" * (MAX_DISPATCH_LOG_RECORDS + 1))
        self.assert_unknown("log_record_limit")

    def test_malformed_structure_sequence_chain_and_timestamp_remain_unknown(self):
        for mutation in ("sequence", "chain", "bool_timestamp", "backward_time", "extra_key", "bad_json"):
            with self.subTest(mutation=mutation):
                self.write(self.pair())
                rows = [json.loads(line) for line in self.path.read_text().splitlines()]
                if mutation == "sequence":
                    rows[1]["seq"] = 3
                elif mutation == "chain":
                    rows[1]["prev"] = "f" * 64
                elif mutation == "bool_timestamp":
                    rows[1]["ts_ms"] = True
                elif mutation == "backward_time":
                    rows[1]["ts_ms"] = 101
                elif mutation == "extra_key":
                    rows[1]["extra"] = "value"
                self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                if mutation == "bad_json":
                    self.path.write_text(self.path.read_text() + "not JSON\n")
                self.assert_unknown("malformed_log")

    def test_duplicate_json_keys_and_unbounded_fired_values_are_not_evidence(self):
        self.write(self.pair())
        self.path.write_text(self.path.read_text().replace('"proposal_id": 53', '"proposal_id": 0, "proposal_id": 53'))
        self.assert_unknown("malformed_log")
        self.write(self.pair(decision={**self.decision, "fired": ["x" * 257]}))
        self.assert_unknown("malformed_decision")

    def test_malformed_numeric_payload_does_not_escape_diagnostic(self):
        for value in (10**1000, float("inf"), float("nan")):
            with self.subTest(value_type=type(value).__name__):
                proposal = copy.deepcopy(self.proposal)
                proposal["action"]["points"][-1]["positions"][0] = value
                self.write(self.pair(proposal))
                self.assert_unknown()

    def test_evidence_owner_and_symlink_boundary_remains_enforced(self):
        self.write(self.pair())
        result = dispatch_denial(self.path, root=self.root, expected_uid=os.geteuid() + 1,
                                 points=self.points, binding=self.binding, sent_sim_ms=100)
        self.assertEqual(result["correlation"], "unknown")
        other = self.root / "other.jsonl"
        self.path.rename(other)
        self.path.symlink_to(other)
        self.assert_unknown("log_unavailable")


if __name__ == "__main__":
    unittest.main()
