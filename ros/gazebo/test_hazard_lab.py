import math
import json
import copy
import tempfile
from pathlib import Path
from xml.etree import ElementTree as E
import unittest
import ast
from types import SimpleNamespace
from unittest import mock
from hazard_lab import (
    Kinematics,
    fresh_sample,
    stationary,
    plan,
    ordinary_completion,
    accepted_plan_matches,
    expected_positions,
    tracking_start_ms,
)
from public_report import report, failed_household_result


class WorldPublicationTest(unittest.TestCase):
    def test_absent_semantics_preserves_observations_but_failure_guard_blocks_them(self):
        # Exercise the actual ROS callback without requiring ROS on the host.
        tree = ast.parse(Path(__file__).with_name("run_reference.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GazeboWorld")
        method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "_publish_world")
        import threading
        import time
        scope = {"time": time, "math": math, "json": json, "ARM_JOINTS": [],
                 "MODEL_NAME": "fixture", "String": SimpleNamespace}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "run_reference.py", "exec"), scope)
        stamp = SimpleNamespace(sec=1, nanosec=0)
        header = SimpleNamespace(stamp=stamp)
        world = SimpleNamespace(
            get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=1_000_000_000)),
            odom=SimpleNamespace(header=header, twist=SimpleNamespace(twist=SimpleNamespace(angular=SimpleNamespace(z=0)))),
            joint=SimpleNamespace(header=header), odom_received=time.monotonic(), joint_received=time.monotonic(),
            heading=lambda: 0, measured_joints=lambda: [], pose=lambda: (5, 5), speed=lambda: 0,
            perception=SimpleNamespace(snapshot=lambda *_: ([], 1, {"healthy": True, "stamp_ms": 1000})),
            human_lock=threading.Lock(), human_motion=None, human=None, native=None,
            semantic_snapshot=lambda _: None, semantic_last={"revision": 1},
            world_pub=mock.Mock(), world_count=0, hazard_guard=None, _emit=mock.Mock(),
            primary_joint=lambda: 0,
        )
        publish = scope["_publish_world"]
        publish(world)
        payload = json.loads(world.world_pub.publish.call_args.args[0].data)
        self.assertIsNone(payload["semantic"])
        self.assertEqual(payload["stamp_ms"], 1000)
        self.assertIsNone(world.semantic_last)
        world.semantic_snapshot = mock.Mock(return_value={"observed_ms": 950})
        publish(world)
        world.semantic_snapshot.assert_called_once_with(1000)
        self.assertEqual(json.loads(world.world_pub.publish.call_args.args[0].data)["semantic"]["observed_ms"], 950)
        world.hazard_guard = lambda: False
        publish(world)
        self.assertEqual(world.world_pub.publish.call_count, 2)


class HazardAdapterTest(unittest.TestCase):
    def test_fk_uses_mount_and_joint_axis(self):
        xml = """<robot><joint name="mount" type="fixed"><parent link="base_link"/><child link="link1"/><origin xyz="0 0 .3"/></joint>
        <joint name="joint1" type="revolute"><parent link="link1"/><child link="link2"/><axis xyz="0 0 1"/></joint>
        <joint name="tip" type="fixed"><parent link="link2"/><child link="end_effector_link"/><origin xyz=".3 0 0"/></joint></robot>"""
        k = Kinematics(xml)
        tip = k.point([math.pi / 2, 0, 0, 0], (5, 5), 0)
        self.assertAlmostEqual(tip["x"], 5)
        self.assertAlmostEqual(tip["y"], 5.3)
        self.assertAlmostEqual(tip["z"], 0.3)
        points = plan([0, 0, 0, 0], 0.75)
        path = k.path(points, (5, 5), 0)
        self.assertEqual(path[0], k.point(points[0][1], (5, 5), 0))
        self.assertEqual(path[-1], k.point(points[-1][1], (5, 5), 0))
        self.assertLess(
            max(math.dist(a.values(), b.values()) for a, b in zip(path, path[1:])),
            0.00061,
        )

    def test_manufacturer_chain_margin_and_fixture_envelopes(self):
        root_path = Path(__file__).resolve().parents[2]
        asset = json.loads((root_path / "sim/assets/rosbot-xl.json").read_text())
        robot = E.Element("robot")
        for joint in asset["joints"]:
            j = E.SubElement(robot, "joint", name=joint["name"], type=joint["type"])
            E.SubElement(j, "parent", link=joint["parent"])
            E.SubElement(j, "child", link=joint["child"])
            E.SubElement(
                j,
                "origin",
                **{
                    key: " ".join(map(str, value))
                    for key, value in joint["origin"].items()
                }
            )
            E.SubElement(j, "axis", xyz=" ".join(map(str, joint["axis"])))
        k = Kinematics(E.tostring(robot))
        home = k.point([0, -1, 0.7, 0.3], (5, 5), 0)
        self.assertAlmostEqual(home["x"], 5.039720693937)
        self.assertAlmostEqual(home["z"], 0.3339485044125)
        lengths = []
        for joint in k.chain:
            origin = joint.find("origin")
            lengths.append(
                math.sqrt(
                    sum(float(v) ** 2 for v in origin.get("xyz", "0 0 0").split())
                )
                if origin is not None
                else 0
            )
        displacement = sum(
            sum(lengths[i + 1 :]) * 0.05
            for i, j in enumerate(k.chain)
            if j.get("type") != "fixed"
        )
        self.assertLess(displacement, 0.07)
        fixtures = json.loads(
            (root_path / "sim/assets/household-fixtures.json").read_text()
        )
        for case, *_ in __import__("hazard_lab").CASES:
            for role in ("item", "target"):
                for part in fixtures[case][role]:
                    half = (
                        [s / 2 for s in part["size"]]
                        if part["shape"] == "box"
                        else (
                            [part["radius"]] * 3
                            if part["shape"] == "sphere"
                            else [part["radius"], part["radius"], part["length"] / 2]
                        )
                    )
                    if role == "item":
                        self.assertLess(
                            math.sqrt(
                                sum(
                                    (abs(p) + h) ** 2 for p, h in zip(part["pos"], half)
                                )
                            ),
                            0.06,
                        )
                    else:
                        for extent, bound in zip(
                            (abs(p) + h for p, h in zip(part["pos"], half)),
                            (0.06, 0.06, 0.09),
                        ):
                            self.assertLessEqual(extent, bound)
        orientation = k.orientation([math.pi / 2, 0.4, -0.4, 0], (5, 5), 0)
        self.assertAlmostEqual(orientation[2], math.sqrt(0.5))
        self.assertAlmostEqual(orientation[3], math.sqrt(0.5))

    def test_wall_and_sim_time_freshness(self):
        self.assertTrue(fresh_sample(({}, 1000, 2.0), 1100, 2.1))
        self.assertFalse(fresh_sample(({}, 1000, 2.0), 1201, 2.1))
        self.assertFalse(fresh_sample(({}, 1000, 2.0), 1100, 2.21))
        self.assertFalse(fresh_sample(None, 1000, 2.1))
        self.assertFalse(fresh_sample(({}, 2000, 2.0), 1000, 2.1))

    def test_wire_status_and_signed_plan_binding(self):
        self.assertTrue(
            ordinary_completion(
                {
                    "mode": "normal",
                    "stop": "no_command",
                    "active": None,
                    "arm_cancelling": False,
                }
            )
        )
        for stop in ("NoCommand", "stale_world", "denied", "revoked", "unarmed"):
            self.assertFalse(ordinary_completion({"mode": "normal", "stop": stop}))
        points = plan([0, 0, 0, 0], 0.3)
        action = {
            "type": "joint_trajectory",
            "ttl_ms": 1000,
            "points": [
                {"time_from_start_ms": ms, "positions": list(q)} for ms, q in points
            ],
        }
        self.assertTrue(accepted_plan_matches(points, action))
        self.assertEqual(
            tracking_start_ms({"expires_ms": 6000, "action": action}), 5000
        )
        self.assertEqual(points[-1][0], 900)
        action["points"][1]["positions"][0] = 0.4
        self.assertFalse(accepted_plan_matches(points, action))
        self.assertAlmostEqual(expected_positions(points, 400)[0], 0.15)
        self.assertAlmostEqual(expected_positions(points, 1200)[0], 0.3)

    def test_moving_base_invalidates_plan(self):
        self.assertTrue(stationary((5, 5), (5, 5), 0, 0, 0))
        self.assertFalse(stationary((5, 5), (5.003, 5), 0, 0, 0))
        self.assertFalse(stationary((5, 5), (5, 5), 0, 0.02, 0))
        self.assertFalse(stationary((5, 5), (5, 5), 0, 0, 0.02))

    def test_incomplete_and_duplicate_evidence_cannot_pass(self):
        partial = {
            "profile": "household_hazards",
            "ok": True,
            "hazard_checks": [{"id": "human", "blocked": True, "allowed": True}],
        }
        result = report(partial)
        partial["hazard_checks"] *= 2
        self.assertEqual(report(partial)["checks"][0]["status"], "failed")
        self.assertNotEqual(result["status"], "passed")
        self.assertEqual(result["scope"], "household_hazard_mandatory_gate_simulation")
        self.assertEqual(result["checks"][0]["status"], "failed")
        self.assertEqual(result["checks"][1]["status"], "not_run")

    def test_report_requires_bound_tracked_motion_and_chemical_retreat(self):
        motion = {
            "allowed": True,
            "mandatory_semantic_gate": True,
            "measured_motion_rad": 0.3,
            "plan_sha256": "a" * 64,
            "signed_arm_acceptance_observed": True,
            "accepted_waypoints_match": True,
            "max_joint_tracking_error_rad": 0.02,
            "tracking_samples": 20,
        }
        rows = []
        for identifier, _, _, _, reason in __import__("hazard_lab").CASES:
            rows.append(
                {
                    **motion,
                    "id": identifier,
                    "blocked": True,
                    "signed_gate_rejection_observed": True,
                    "reason": reason,
                    "denied_drift_rad": 0.001,
                }
            )
        chemical_motion = {**motion, "steps": [dict(motion), dict(motion)]}
        rows[4].update(
            retained_contents=["bleach"],
            first_transfer=copy.deepcopy(chemical_motion),
            retreat=copy.deepcopy(chemical_motion),
            item_label_sequence=["bleach", "ammonia"],
            same_visual_prop=True,
        )
        result = {
            "profile": "household_hazards",
            "ok": True,
            "hazard_checks": rows,
            "negative_controls": {
                name: {"allowed": False, "reason": reason}
                for name, reason in (
                    ("stale", "perception:stale"),
                    ("unknown_item", "item:unknown"),
                    ("missing_coverage", "perception:coverage-unknown"),
                )
            },
            "mandatory_gate_controls": {
                key: {"allowed": False, "reason": reason,
                      "signed_gate_rejection_observed": True, "denied_drift_rad": 0.001}
                for key, reason in (("missing_binding", "household:missing-binding"),
                                    ("wrong_revision", "household:binding-mismatch"),
                                    ("wrong_item", "household:binding-mismatch"))},
        }
        self.assertEqual(report(result)["status"], "passed")
        for field, bad in (
            ("accepted_waypoints_match", False),
            ("max_joint_tracking_error_rad", 0.051),
            ("tracking_samples", 9),
            ("mandatory_semantic_gate", False),
            ("signed_gate_rejection_observed", False),
        ):
            changed = copy.deepcopy(result)
            changed["hazard_checks"][0][field] = bad
            self.assertEqual(report(changed)["status"], "failed")
        changed = copy.deepcopy(result)
        del changed["hazard_checks"][4]["retreat"]
        self.assertEqual(report(changed)["status"], "failed")
        changed = copy.deepcopy(result)
        changed["hazard_checks"][4]["first_transfer"]["steps"][0][
            "tracking_samples"
        ] = 0
        self.assertEqual(report(changed)["status"], "failed")
        result["ok"] = False
        self.assertEqual(report(result)["status"], "failed")

    def test_failed_report_preserves_completed_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            row = {
                "id": "human",
                "blocked": True,
                "allowed": True,
                "mandatory_semantic_gate": True,
                "signed_gate_rejection_observed": True,
                "reason": "human:protected-volume",
                "denied_drift_rad": 0.001,
                "measured_motion_rad": 0.3,
                "plan_sha256": "a" * 64,
                "signed_arm_acceptance_observed": True,
                "accepted_waypoints_match": True,
                "max_joint_tracking_error_rad": 0.01,
                "tracking_samples": 20,
            }
            (root / "hazard-progress.json").write_text(json.dumps([row]))
            (root / "hazard-diagnostics.json").write_text(json.dumps({"case": "heat"}))
            summary = report(failed_household_result(root), failed=True)
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(
                [r["status"] for r in summary["checks"][:3]],
                ["passed", "failed", "not_run"],
            )
