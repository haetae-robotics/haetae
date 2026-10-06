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
    setup_reset_ready,
    accepted_plan_matches,
    expected_positions,
    tracking_start_ms,
)
from public_report import report, failed_household_result


class WorldPublicationTest(unittest.TestCase):
    def test_speculative_judge_waits_for_original_simulation_stamps(self):
        tree = ast.parse(Path(__file__).with_name("hazard_lab.py").read_text())
        lab = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_lab")
        method = next(n for n in lab.body if isinstance(n, ast.FunctionDef) and n.name == "fresh_request")
        samples = iter([{"now_ms": 1000, "observed_ms": 800},
                        {"now_ms": 1000, "observed_ms": 1001},
                        {"now_ms": 1000, "observed_ms": 925},
                        {"now_ms": 1000, "observed_ms": 926}])
        seen = []
        def request(*args):
            sample = next(samples)
            seen.append(sample.copy())
            return sample
        def wait(check, timeout, processes, description):
            self.assertEqual(timeout, 3)
            for _ in range(4):
                checked = check()
                if checked:
                    # The production wait_for returns a wall-clock timestamp.
                    return 10.0
            raise TimeoutError(description)
        scope = {"request": request, "wait_for": wait, "processes": {}}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual-household-freshness", "exec"), scope)
        self.assertEqual(scope["fresh_request"]("knife", []), seen[-1])
        self.assertEqual([row["observed_ms"] for row in seen], [800, 1001, 925, 926])
        scope["request"] = lambda *args: {"now_ms": 1000, "observed_ms": 800}
        with self.assertRaises(TimeoutError):
            scope["fresh_request"]("knife", [])

    def test_first_fixture_cannot_dispatch_repositioning_before_setup(self):
        tree = ast.parse(Path(__file__).with_name("hazard_lab.py").read_text())
        check = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                     and any(isinstance(child, ast.Constant) and child.value ==
                             "household fixture profile requires initial joint1 within 0.1 rad"
                             for child in ast.walk(n)))
        code = compile(ast.Module(body=[check], type_ignores=[]), "actual-initial-posture-gate", "exec")
        for joint in (0., .1, -.1):
            exec(code, {"index": 1, "initial_joint": joint, "math": math})
        for joint in (.10001, -.10001, float("nan"), float("inf")):
            with self.assertRaises(RuntimeError):
                exec(code, {"index": 1, "initial_joint": joint, "math": math})
        exec(code, {"index": 2, "initial_joint": .2, "math": math})

    def setup_world(self):
        semantic = {"observed_ms": 995, "revision": 2, "task_revision": 1,
                    "coverage_known": True, "confidence": 1}
        state = {"mode": "normal", "stop": "denied", "armed": [], "active": None,
                 "recorder_ok": True, "state_ok": True, "arm_cancelling": False,
                 "arm_controller_ready": True, "world_age_ms": 5,
                 "semantic_revision": 2, "semantic_task_revision": 1}
        return SimpleNamespace(states=[(9.95, state)], sensor_info={"healthy": True},
            semantic_last=semantic, odom_received=9.95, joint_received=9.95,
            joint=SimpleNamespace(name=["joint1", "joint2", "joint3", "joint4"], velocity=[0.] * 4),
            speed=lambda: 0., outcomes=[], controllers_unlocked=lambda: True)

    def test_setup_reset_requires_fresh_accepted_world_and_every_actuator_stopped(self):
        world = self.setup_world()
        self.assertTrue(setup_reset_ready(world, 1000, 10.))
        self.assertFalse(ordinary_completion(world.states[-1][1]))
        for key, value in (("active", {"id": 1}), ("arm_cancelling", True),
                           ("recorder_ok", False), ("state_ok", False), ("mode", "hazard"),
                           ("world_age_ms", 75), ("semantic_revision", 3)):
            invalid = self.setup_world()
            invalid.states[-1][1][key] = value
            self.assertFalse(setup_reset_ready(invalid, 1000, 10.))
        for key in ("odom_received", "joint_received"):
            invalid = self.setup_world()
            setattr(invalid, key, 9.89)
            self.assertFalse(setup_reset_ready(invalid, 1000, 10.))
        for velocities in ([0., 0., .04, 0.], [0.] * 3, [0., 0., float("nan"), 0.]):
            invalid = self.setup_world()
            invalid.joint.velocity = velocities
            self.assertFalse(setup_reset_ready(invalid, 1000, 10.))
        world.semantic_last["observed_ms"] = 900
        self.assertFalse(setup_reset_ready(world, 1000, 10.))

    def test_initial_explicit_setup_reset_cannot_run_after_an_arm_dispatch_or_denial(self):
        tree = ast.parse(Path(__file__).with_name("hazard_lab.py").read_text())
        lab = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_lab")
        method = next(n for n in lab.body if isinstance(n, ast.FunctionDef) and n.name == "initialize_for_test")
        world = self.setup_world()
        stops = []
        def stop(value):
            stops.append(value)
            world.states[-1][1].update(stop="no_command", armed=["vla"])
            world.outcomes.append((10., {"decision": {"verdict": "yun", "action": {"type": "stop"}}}))
        world.propose_base = stop
        def wait(check, *args, action=None):
            for _ in range(4):
                if check():
                    return True
                if action:
                    action()
            raise TimeoutError("unqualified setup must not reset")
        scope = {"world": world, "arm_dispatch": {"sent": False, "initialized": False},
                 "verified_denial": {"at": None}, "current_binding": lambda *_: {},
                 "setup_reset_ready": setup_reset_ready, "now": lambda: 1000, "wait_for": wait,
                 "processes": {}, "time": SimpleNamespace(monotonic=lambda: 10.)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual-household-setup", "exec"), scope)
        scope["initialize_for_test"]("knife", [])
        self.assertEqual(stops, [0., 0.])
        for flag, denial in (({"sent": True, "initialized": False}, None),
                             ({"sent": False, "initialized": True}, None),
                             ({"sent": False, "initialized": False}, 9.)):
            scope["arm_dispatch"], scope["verified_denial"]["at"] = flag, denial
            with self.assertRaises(RuntimeError):
                scope["initialize_for_test"]("knife", [])
        self.assertEqual(stops, [0., 0.])

    def test_hazard_rejection_binds_after_explicit_rearm_queue_drains(self):
        # Exercise the actual fixture dispatcher against an observer revision
        # that changes while the explicit STOP/rearm queue drains.
        from typing import Union, Callable, Literal
        tree = ast.parse(Path(__file__).with_name("hazard_lab.py").read_text())
        lab = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_lab")
        method = next(n for n in lab.body if isinstance(n, ast.FunctionDef) and n.name == "reject_at_gate")
        revision = {"value": 1}
        world = SimpleNamespace(arm_positions=lambda: [0] * 4, outcomes=[])

        def rearm():
            revision["value"] = 2

        def submit(points, semantic):
            fired = "household:human" if semantic["world_revision"] == revision["value"] else "household:binding-mismatch"
            world.outcomes.append((10, {"decision": {"verdict": "bul", "fired": [fired]}}))

        def wait(check, *_):
            result = check()
            if not result:
                raise TimeoutError("expected hazard verdict, not stale fixture reference")
            return result

        world.propose_arm_plan = submit
        scope = {"Union": Union, "Callable": Callable, "Literal": Literal,
                 "world": world, "current_binding": lambda *_: {"world_revision": revision["value"]},
                 "rearm_for_test": rearm, "wait_for": wait, "processes": {},
                 "verified_denial": {"at": None},
                 "arm_dispatch": {"sent": False, "initialized": False},
                 "time": SimpleNamespace(monotonic=lambda: 10, sleep=lambda _: None)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "hazard_lab.py", "exec"), scope)
        result = scope["reject_at_gate"]([], "inert", expected="household:human")
        self.assertTrue(result["signed_gate_rejection_observed"])

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
        # Unknown coverage must reach the root policy immediately as confidence
        # zero; waiting for the old world to age adds simulation-time latency.
        world.perception = SimpleNamespace(snapshot=lambda *_: ([], 0, {"healthy": False, "stamp_ms": 900}))
        publish(world)
        payload = json.loads(world.world_pub.publish.call_args.args[0].data)
        self.assertEqual(payload["confidence"], 0)
        self.assertEqual(payload["stamp_ms"], 1000)
        world.hazard_guard = lambda: False
        publish(world)
        self.assertEqual(world.world_pub.publish.call_count, 3)
        world.hazard_guard = None
        world.odom_received = time.monotonic() - 1
        publish(world)
        self.assertEqual(world.world_pub.publish.call_count, 3)
        world.odom_received = time.monotonic()
        world.joint.header = SimpleNamespace(stamp=SimpleNamespace(sec=0, nanosec=700_000_000))
        publish(world)
        self.assertEqual(world.world_pub.publish.call_count, 3)

    def test_advancing_observation_keeps_source_stamp_and_periodic_negative_updates(self):
        tree = ast.parse(Path(__file__).with_name("run_reference.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GazeboWorld")
        method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "_publish_world")
        import threading
        clock = SimpleNamespace(nanoseconds=1_100_000_000)
        sensor = {"healthy": True, "stamp_ms": 1040}
        scope = {"time": SimpleNamespace(monotonic=lambda: 10), "math": math, "json": json,
                 "ARM_JOINTS": [], "MODEL_NAME": "fixture", "String": SimpleNamespace}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "run_reference.py", "exec"), scope)
        world = SimpleNamespace(
            get_clock=lambda: SimpleNamespace(now=lambda: clock),
            odom=SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=1, nanosec=70_000_000)),
                twist=SimpleNamespace(twist=SimpleNamespace(angular=SimpleNamespace(z=0)))),
            joint=SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=1, nanosec=80_000_000))),
            odom_received=10, joint_received=10, heading=lambda: 0, measured_joints=lambda: [],
            pose=lambda: (5, 5), speed=lambda: 0, primary_joint=lambda: 0,
            perception=SimpleNamespace(snapshot=lambda *_: ([], int(sensor["healthy"]), dict(sensor))),
            human_lock=threading.Lock(), human_motion=None, human=None, native=None,
            semantic_snapshot=lambda stamp: {"observed_ms": stamp}, semantic_last=None,
            world_pub=mock.Mock(), world_count=0, published_world_stamp=None,
            hazard_guard=None, _emit=mock.Mock())
        publish = scope["_publish_world"]
        publish(world, only_advanced=True)
        payload = json.loads(world.world_pub.publish.call_args.args[0].data)
        self.assertEqual(payload["stamp_ms"], 1040)  # Oldest source, not current clock/joint.
        self.assertEqual(payload["semantic"]["observed_ms"], 1040)
        clock.nanoseconds += 10_000_000
        publish(world, only_advanced=True)
        sensor["stamp_ms"] = 1030
        publish(world, only_advanced=True)
        self.assertEqual(world.world_pub.publish.call_count, 1)  # No repeated/frozen refresh.
        sensor["stamp_ms"] = 1050
        world.hazard_guard = lambda: False
        publish(world, only_advanced=True)
        self.assertEqual(world.published_world_stamp, 1040)
        world.hazard_guard = None
        publish(world, only_advanced=True)
        self.assertEqual(world.world_pub.publish.call_count, 2)
        self.assertEqual(world.published_world_stamp, 1050)
        # A negative/semantic observation at the same old source stamp still
        # reaches the root on the periodic path; extra event suppression must
        # not suppress the existing timer's revocation behavior.
        world.semantic_snapshot = lambda _: None
        sensor["healthy"] = False
        publish(world, only_advanced=True)
        self.assertEqual(world.world_pub.publish.call_count, 2)
        publish(world)
        payload = json.loads(world.world_pub.publish.call_args.args[0].data)
        self.assertEqual(payload["confidence"], 0)
        self.assertIsNone(payload["semantic"])
        self.assertEqual(payload["stamp_ms"], 1080)  # Original joint observation, no restamp.
        self.assertEqual(world.world_pub.publish.call_count, 3)


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
            "history_motion_settled": True,
            "material_effects_committed": 0,
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
            "history_controls": {
                "consumed_step": {"blocked": True, "reason": "history:consumed-step",
                    "settled_positive_control": True, "fresh_counter_signed_probe": True, "denied_drift_rad": 0.001},
                "retained_contents": {"raw_contents_empty": True, "retained_contaminants": 1,
                    "reason": "household:chemicals:incompatible", "signed_gate_rejection_observed": True,
                    "settled_positive_control": True, "denied_drift_rad": 0.001}},
            "material_effects_committed": 0,
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
        for name, field, bad in (("consumed_step", "fresh_counter_signed_probe", False),
                                 ("retained_contents", "raw_contents_empty", False),
                                 ("retained_contents", "retained_contaminants", 0)):
            changed = copy.deepcopy(result)
            changed["history_controls"][name][field] = bad
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
                "accepted_waypoints_match": True, "history_motion_settled": True, "material_effects_committed": 0,
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
