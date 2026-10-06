"""World revocation must advance a scene even without a proposal Decision."""

import json
from pathlib import Path
import tempfile
import unittest
import ast
import math
from types import SimpleNamespace
from stop_evidence import world_expiry_stop_observed, sensor_stop_report

from stop_evidence import person_stop_observed, person_stop_report


class ProbeEntryStopTest(unittest.TestCase):
    def test_every_trusted_probe_world_preserves_measured_twist_and_source_stamp(self):
        tree = ast.parse(Path(__file__).with_name('controller_probes.py').read_text())
        method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'measured_world')
        names = ['joint1', 'joint2', 'joint3', 'joint4']
        def header(ms):
            return SimpleNamespace(stamp=SimpleNamespace(sec=ms // 1000, nanosec=ms % 1000 * 1_000_000))
        world = SimpleNamespace(
            odom=SimpleNamespace(header=header(1020), pose=SimpleNamespace(pose=SimpleNamespace(
                position=SimpleNamespace(x=1.2, y=.5), orientation=SimpleNamespace(x=0., y=0., z=0., w=1.))),
                twist=SimpleNamespace(twist=SimpleNamespace(linear=SimpleNamespace(x=.2), angular=SimpleNamespace(z=.7)))),
            joint=SimpleNamespace(header=header(1060), name=names, position=[.1]*4, velocity=[.01]*4))
        scope = dict(world=world, ARM_JOINTS=names, math=math)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'actual-probe-world', 'exec'), scope)
        scope['world_snapshot'] = lambda: scope['measured_world'](world)
        # Execute every real world-payload expression, covering both initial
        # admission and arm renewal instead of only testing the shared helper.
        payloads = [call.args[1] for call in ast.walk(tree) if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name) and call.func.id == 'feed'
                    and len(call.args) == 2 and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == 'world']
        self.assertEqual(len(payloads), 2)
        snapshots = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'snapshot' for target in node.targets)]
        self.assertEqual(len(snapshots), 2)
        for expression in snapshots:
            self.assertEqual(ast.unparse(expression), 'world_snapshot()')
        for angular in (.7, -.8, float('nan'), float('inf')):
            world.odom.twist.twist.angular.z = angular
            scope['snapshot'] = scope['measured_world'](world)
            for expression in payloads:
                payload = eval(compile(ast.Expression(expression), 'actual-world-ingress', 'eval'), scope)
                self.assertEqual(payload['robot']['twist']['linear'], .2)
                if math.isnan(angular):
                    self.assertTrue(math.isnan(payload['robot']['twist']['angular']))
                else:
                    self.assertEqual(payload['robot']['twist']['angular'], angular)
                self.assertEqual(payload['robot']['pose'], {'x': 6.2, 'y': 5.5})
                self.assertEqual(payload['stamp_ms'], 1020)
        world.joint.header = header(980)
        self.assertEqual(scope['measured_world'](world)['stamp_ms'], 980)

    def test_rejected_or_mismatched_world_cannot_propose_or_renew(self):
        tree = ast.parse(Path(__file__).with_name('controller_probes.py').read_text())
        nodes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        helper = nodes['require_world_accepted']
        # Execute the actual two ingress functions, including their acceptance
        # checks. No proposal or permit may follow a rejected world response.
        approve = nodes['approve']
        approve.body = [node for node in approve.body if not isinstance(node, ast.Nonlocal)]
        snapshot = {'stamp_ms': 980}
        invalid = [{}, {'rejected': {'error': 'invalid world'}},
                   {'world_updated': {'stamp_ms': 981}}, {'world_updated': {'stamp_ms': True}}]
        for name in ('approve', 'renew'):
            for outcome in invalid:
                calls = []
                scope = dict(json=json, world_snapshot=lambda: snapshot, proposal_id=0,
                    now=lambda: 1000, time=SimpleNamespace(monotonic_ns=lambda: 10_000_000),
                    feed=lambda role, value: calls.append((role, value)) or {'outcome': outcome},
                    send=lambda packet: self.fail('rejected world forwarded a permit'))
                exec(compile(ast.Module(body=[helper, nodes[name]], type_ignores=[]),
                             'actual-world-admission', 'exec'), scope)
                with self.assertRaisesRegex(AssertionError, 'measured world was not accepted'):
                    scope[name]({'type': 'velocity'}) if name == 'approve' else scope[name]()
                self.assertEqual(calls, [('world', snapshot)])
        scope = dict(json=json)
        exec(compile(ast.Module(body=[helper], type_ignores=[]), 'actual-world-accepted', 'exec'), scope)
        scope['require_world_accepted']({'outcome': {'world_updated': {'stamp_ms': 980}}}, snapshot)

    def test_actual_world_snapshot_requires_fresh_feedback(self):
        tree = ast.parse(Path(__file__).with_name('controller_probes.py').read_text())
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == 'world_snapshot')
        world = SimpleNamespace(joint_received=9.99, odom_received=9.99)
        def wait_for(predicate, *args):
            if not predicate():
                raise TimeoutError('fresh feedback required')
        scope = dict(world=world, processes={}, wait_for=wait_for,
                     time=SimpleNamespace(monotonic=lambda: 10.), measured_world=lambda value: value)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'actual-feedback-wait', 'exec'), scope)
        self.assertIs(scope['world_snapshot'](), world)
        for joint, odom in ((9.8, 9.99), (9.99, 9.8), (10.01, 9.99), (9.99, 10.01)):
            world.joint_received, world.odom_received = joint, odom
            with self.assertRaises(TimeoutError):
                scope['world_snapshot']()

    def test_maintenance_requires_fresh_base_and_every_arm_joint_stopped(self):
        tree = ast.parse(Path(__file__).with_name('controller_probes.py').read_text())
        exercise = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'exercise')
        method = next(node for node in exercise.body if isinstance(node, ast.FunctionDef) and node.name == 'stopped')
        names = ['joint1', 'joint2', 'joint3', 'joint4']
        world = SimpleNamespace(joint=SimpleNamespace(name=names[:], velocity=[0.] * 4),
                                joint_received=9.95, odom_received=9.95,
                                odom=SimpleNamespace(twist=SimpleNamespace(twist=SimpleNamespace(
                                    linear=SimpleNamespace(x=0.), angular=SimpleNamespace(z=0.)))))
        scope = dict(world=world, ARM_JOINTS=names, math=math,
                     time=SimpleNamespace(monotonic=lambda: 10.))
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'actual-probe-stop', 'exec'), scope)
        stopped = scope['stopped']
        self.assertTrue(stopped())
        for values in ([0., 0., .04, 0.], [0.] * 3, [0., float('nan'), 0., 0.]):
            world.joint.velocity = values
            self.assertFalse(stopped())
        world.joint.velocity = [0.] * 4
        world.joint.name = names[:-1]
        self.assertFalse(stopped())
        world.joint.name = names[:]
        world.joint_received = 9.89
        self.assertFalse(stopped())
        world.joint_received, world.odom_received = 9.95, 9.89
        self.assertFalse(stopped())
        world.odom_received = 9.95
        world.odom.twist.twist.linear.x = .04
        self.assertFalse(stopped())
        world.odom.twist.twist.linear.x = 0.
        for angular in (.04, -.04, float('nan'), float('inf')):
            world.odom.twist.twist.angular.z = angular
            self.assertFalse(stopped())
        world.odom.twist.twist.angular.z = 0.
        self.assertTrue(stopped())
        world.odom = None
        self.assertFalse(stopped())
        world.joint = None
        self.assertFalse(stopped())


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

    def test_world_admission_cutoff_requires_named_cause_disarm_and_later_zero(self):
        error = "trusted world has insufficient controller admission budget"
        outcomes = [(10.04, {"rejected": {"error": error}})]
        states = [(10.06, {"stop": "unarmed", "armed": [], "active": None})]
        self.assertEqual(self.check([], states=states, outcomes=outcomes, require_perception=False),
                         {"stop_reason": "world_admission_budget_exhausted", "zero_at": 10.05})
        self.assertIsNone(self.check([], states=states, outcomes=outcomes))
        for rejected in ([], [(9.99, {"rejected": {"error": error}})],
                         [(10.04, {"rejected": {"error": "original controller authority has insufficient admission budget"}})]):
            self.assertIsNone(self.check([], states=states, outcomes=rejected, require_perception=False))
        for bad_state in ({"stop": "unarmed", "armed": ["vla"]},
                          {"stop": "unarmed", "armed": [], "active": {"id": 1}},
                          {"stop": "no_command", "armed": []}):
            self.assertIsNone(self.check([], states=[(10.06, bad_state)], outcomes=outcomes,
                                         require_perception=False))
        self.assertIsNone(self.check([], states=[(10.03, states[0][1])], outcomes=outcomes,
                                     require_perception=False))
        for zeros in ([], [(10.03, 1050)]):
            self.assertIsNone(self.check([], states=states, outcomes=outcomes, zeros=zeros,
                                         require_perception=False))

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
