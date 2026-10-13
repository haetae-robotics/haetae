import ast
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import hazard_lab
from hazard_diagnostics import (DispatchWatch, ObservationSnapshot, ObservationUnavailable,
                                OmissionHistory, observation_snapshot, outcome_summary)


class SemanticOmissionTest(unittest.TestCase):
    def setUp(self):
        self.clock = {"sim": 1000, "wall": 10.0}
        self.stamp = SimpleNamespace(sec=1, nanosec=0)
        self.world = SimpleNamespace(
            joint=SimpleNamespace(header=SimpleNamespace(stamp=self.stamp)),
            odom=SimpleNamespace(header=SimpleNamespace(stamp=self.stamp)),
            joint_received=9.95, odom_received=9.96,
            arm_positions=lambda: [0.] * 4, speed=lambda: 0.,
            pose=lambda: (0., 0.), heading=lambda: 0.)
        self.samples = {name: ({"x": 0., "y": 0., "z": 0.}, 990, 9.98)
                        for name in ("hazard_chemicals_target", "hazard_chemicals_item",
                                     "hazard_human_target", "hazard_human_item")}
        self.native = SimpleNamespace(sample=self.samples.get, failure=None)
        self.omissions = OmissionHistory()
        self.scope = {
            "active": {"case": "chemicals", "stage": 5},
            "native": self.native, "world": self.world,
            "time": SimpleNamespace(monotonic=lambda: self.clock["wall"]),
            "now": lambda: self.clock["sim"], "semantic_lock": threading.RLock(),
            "semantic_context": {"item": "ammonia", "contents": [], "task_revision": 16,
                "task_id": "household-lab-chemicals", "step_id": "fixture-motion-16",
                "revision": 24, "facts": None, "base_pose": {"x": 0., "y": 0.}, "base_yaw": 0.},
            "policy_binding": {"robot_id": "fixture", "model_sha256": "0" * 64, "tool_id": "fixture"},
            "kinematics": SimpleNamespace(path=lambda *_: [], point=lambda *_: {"x": 0., "y": 0., "z": 0.}),
            "fresh_sample": hazard_lab.fresh_sample, "CASES": hazard_lab.CASES,
            "observation_snapshot": observation_snapshot, "ObservationUnavailable": ObservationUnavailable,
            "ObservationSnapshot": ObservationSnapshot, "omissions": self.omissions, "json": json,
        }
        tree = ast.parse(Path(hazard_lab.__file__).read_text())
        lab = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_lab")
        methods = [node for node in lab.body if isinstance(node, ast.FunctionDef)
                   and node.name in ("request", "semantic_snapshot", "current_observations", "record_omission")]
        exec(compile(ast.Module(body=methods, type_ignores=[]), "actual-semantic-omission", "exec"), self.scope)

    def snapshot(self):
        return self.scope["semantic_snapshot"](985)

    def test_valid_observations_still_publish_original_fused_stamp(self):
        result = self.snapshot()
        self.assertEqual(result["observed_ms"], 985)
        self.assertEqual(result["step_id"], "fixture-motion-16")
        self.assertEqual(self.omissions.count, 0)

    def test_stale_target_records_original_sources_and_does_not_substitute_semantics(self):
        point, _, received = self.samples["hazard_chemicals_target"]
        self.samples["hazard_chemicals_target"] = (point, 799, received)
        self.assertIsNone(self.snapshot())
        self.clock.update(sim=2000, wall=20.)
        self.stamp.sec = 2
        self.world.joint_received = 20.
        self.samples["hazard_chemicals_target"] = (point, 2000, 20.)
        row = self.omissions.report()["recent"][0]
        self.assertEqual(row["reasons"], ["target_missing_or_stale"])
        self.assertEqual(row["fused_stamp_ms"], 985)
        self.assertEqual(row["task_revision"], 16)
        observation = row["observation"]
        self.assertEqual(observation["now_sim_ms"], 1000)
        self.assertEqual(observation["now_wall"], 10.)
        self.assertEqual(observation["sources"]["target"]["age_sim_ms"], 201)
        self.assertEqual(observation["sources"]["joint"]["stamp_ms"], 1000)
        self.assertAlmostEqual(observation["sources"]["joint"]["age_wall_ms"], 50.)
        self.assertEqual(set(observation["sources"]), {"item", "target", "joint", "odom"})

    def test_each_missing_or_stale_input_reports_the_actual_failed_check(self):
        for changed, expected in (("item", "item_missing_or_stale"),
                                  ("joint", "joint_receive_stale"),
                                  ("odom", "odom_receive_stale"),
                                  ("native", "native_fixture_failure"),
                                  ("speed", "base_not_stationary")):
            with self.subTest(changed=changed):
                self.setUp()
                if changed == "item":
                    self.samples.pop("hazard_chemicals_item")
                elif changed in ("joint", "odom"):
                    setattr(self.world, changed + "_received", 9.79)
                elif changed == "native":
                    self.native.failure = "arbitrary private exception payload"
                else:
                    self.world.speed = lambda: .01
                self.assertIsNone(self.snapshot())
                report = self.omissions.report()
                self.assertEqual(report["recent"][0]["reasons"], [expected])
                self.assertNotIn("private", json.dumps(report))

    def test_generic_exception_records_class_only_and_retention_is_bounded(self):
        self.world.arm_positions = mock.Mock(side_effect=ValueError("private payload"))
        for _ in range(40):
            self.assertIsNone(self.snapshot())
        report = self.omissions.report()
        self.assertEqual(report["total"], 40)
        self.assertEqual(len(report["recent"]), 32)
        self.assertEqual(report["recent"][0]["reasons"], ["ValueError"])
        self.assertNotIn("private", json.dumps(report))

    def test_container_omission_keeps_mechanical_and_fixture_stamps(self):
        self.scope["active"].update(case="human", stage=1)
        self.samples["hazard_chemicals_target"] = ({}, 799, 9.98)
        self.assertIsNone(self.snapshot())
        row = self.omissions.report()["recent"][0]
        self.assertEqual(row["reasons"], ["container_missing_or_stale"])
        self.assertEqual(set(row["observation"]["sources"]), {"target", "item", "joint", "odom", "container"})


class OmissionHistoryConcurrencyTest(unittest.TestCase):
    def test_append_during_render_preserves_coherent_snapshot_without_blocking_writer(self):
        history = OmissionHistory()

        def record(index):
            history.record(("item_missing_or_stale",), ObservationSnapshot(index, float(index), ()),
                           case="chemicals", task_revision=index,
                           step_id=f"fixture-motion-{index}", fused_stamp_ms=index)

        for index in range(32):
            record(index)
        rendering = threading.Event()
        appended = threading.Event()
        errors = []
        original_report = ObservationSnapshot.report

        def paused_report(snapshot):
            if snapshot.sim_ms == 0:
                rendering.set()
                self.assertTrue(appended.wait(2), "rendering must not hold the history lock")
            return original_report(snapshot)

        def append_during_render():
            try:
                if not rendering.wait(2):
                    raise AssertionError("report did not begin rendering")
                record(32)
            except Exception as exc:
                errors.append(exc)
            finally:
                appended.set()

        writer = threading.Thread(target=append_during_render)
        with mock.patch.object(ObservationSnapshot, "report", paused_report):
            writer.start()
            try:
                report = history.report()
            finally:
                writer.join(timeout=2)
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(report["total"], 32)
        self.assertEqual(report["retained_limit"], 32)
        self.assertEqual([row["task_revision"] for row in report["recent"]], list(range(32)))
        after = history.report()
        self.assertEqual(after["total"], 33)
        self.assertEqual([row["task_revision"] for row in after["recent"]], list(range(1, 33)))


class DispatchWatchTest(unittest.TestCase):
    def watch(self, evidence):
        return DispatchWatch(evidence, points=[(0, [0.] * 4), (900, [.5, 0., 0., 0.])],
                             binding={"step_id": "fixture-motion-16"}, sent_sim_ms=1000, sent_wall=10.)

    def outcome(self, identity, *, wall=10.01, verdict="bul"):
        return wall, {"decision": {"proposal_id": identity, "verdict": verdict}}

    def test_exact_proposal_denial_raises_before_goal_timeout(self):
        evidence = mock.Mock(return_value={"correlation": "exact", "proposal_id": 53,
                                           "fired": ["household:missing-world"]})
        watch = self.watch(evidence)
        with self.assertRaisesRegex(RuntimeError, "proposal_id=53.*household:missing-world"):
            watch.check([self.outcome(53)])
        evidence.assert_called_once()
        self.assertEqual(evidence.call_args.kwargs["sent_sim_ms"], 1000)

    def test_stale_and_anonymous_rejections_cannot_trigger_exact_attribution(self):
        evidence = mock.Mock()
        watch = self.watch(evidence)
        watch.check([self.outcome(50, wall=9.99), self.outcome(51, verdict="yun"),
                     (10.01, {"rejected": {"error": "unarmed"}})])
        evidence.assert_not_called()
        self.assertEqual(watch.result["correlation"], "unknown")
        self.assertEqual(outcome_summary({"rejected": {"error": "unarmed"}}),
                         {"proposal_id": None, "verdict": None, "fired": None,
                          "rejected_error": "unarmed", "rejected_correlation": "unknown"})

    def test_unrelated_denial_remains_unknown_and_later_exact_denial_fails(self):
        evidence = mock.Mock(side_effect=[{"correlation": "unknown", "reason": "not_found"},
            {"correlation": "exact", "proposal_id": 53, "fired": ["household:missing-world"]}])
        watch = self.watch(evidence)
        watch.check([self.outcome(52)])
        watch.check([self.outcome(52)])
        self.assertEqual(evidence.call_count, 1)
        with self.assertRaises(RuntimeError):
            watch.check([self.outcome(52), self.outcome(53)])

    def test_outcome_strings_and_fired_entries_have_explicit_size_bounds(self):
        summary = outcome_summary({"decision": {"fired": ["x" * 1000] * 100},
                                   "rejected": {"error": "y" * 1000}})
        self.assertEqual(len(summary["fired"]), 32)
        self.assertEqual(len(summary["fired"][0]), 256)
        self.assertEqual(len(summary["rejected_error"]), 256)

    def test_unknown_evidence_reads_are_bounded_and_do_not_change_wait_verdict(self):
        evidence = mock.Mock(return_value={"correlation": "unknown", "reason": "partial_log"})
        watch = self.watch(evidence)
        outcomes = [self.outcome(identity) for identity in range(10)]
        for _ in range(100):
            watch.check(outcomes)
        self.assertEqual(evidence.call_count, 3)
        self.assertEqual(len(watch.seen), 3)
        self.assertEqual(watch.result["correlation"], "unknown")


if __name__ == "__main__":
    unittest.main()
