"""World revocation must advance a scene even without a proposal Decision."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import ast
import math
from collections import deque
from functools import partial
from types import SimpleNamespace
from stop_evidence import world_expiry_stop_observed, sensor_stop_report

from public_report import SENSOR_STOP_REASONS
from stop_evidence import (WORLD_AUTHORITY_CUTS, coverage_cut_first, coverage_cut_record, motion_after_stop, person_stop_observed,
                           person_stop_report)


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

    def test_every_returned_stop_reason_is_named_in_the_report_contract(self):
        error = "trusted world has insufficient controller admission budget"
        disarmed = [(10.06, {"stop": "denied", "armed": [], "active": None})]
        reports = [self.check(self.rows()),
                   self.check([], states=[(10.05, {"stop": "stale_world", "armed": []})], require_perception=False),
                   self.check([], states=disarmed, outcomes=[(10.04, {"rejected": {"error": error}})],
                              require_perception=False)]
        for error in ("stale actuation response: world expired",
                      "trusted world did not advance within dual-clock freshness"):
            reports.append(self.check([], states=disarmed, outcomes=[(10.06, {"rejected": {"error": error}})],
                                      require_perception=False))
        self.assertEqual({report["stop_reason"] for report in reports}, set(SENSOR_STOP_REASONS))


# Main run 38106819587 (9622001), coverage case, harness wall seconds (fault at 10.0,
# Gazebo at about 0.45x real time): proposal 213 was active when the gate refused to
# sign for the world-admission budget (sillok 1429, 1430 Denied). Its zero left before
# the reject outcome. The confidence-0 world (stamp 67499, sillok 1432) was accepted
# after the cut, then a 0.12 proposal met a disarmed source (1433 Unarmed).
CUT = "trusted world has insufficient controller admission budget"
ARMED = {"stop": None, "armed": ["vla"], "active": {"id": 213}}
DENIED = {"stop": "denied", "armed": [], "active": None}
UNARMED = {"stop": "unarmed", "armed": [], "active": None}


def failing_run():
    return dict(
        states=[(10.02, dict(ARMED)), (10.21, dict(ARMED)), (10.219, DENIED), (10.234, DENIED), (10.31, UNARMED)],
        outcomes=[(10.08, {"world_updated": {"stamp_ms": 67387}}), (10.12, {"world_updated": {"stamp_ms": 67387}}),
                  (10.21, {"decision": {"proposal_id": 213, "verdict": "yun"}}),
                  (10.22, {"rejected": {"error": CUT}}), (10.235, {"world_updated": {"stamp_ms": 67499}}),
                  (10.31, {"rejected": {"error": "source must send a zero command to arm"}})],
        zeros=deque([(10.218, 67501), (10.236, 67503), (10.31, 67535)], maxlen=512),
        commands=[(10.02, .12), (10.21, .12), (10.218, 0.), (10.236, 0.), (10.31, 0.)],
        published=deque([(67354, 1.), (67387, 1.), (67387, 1.), (67499, 0.), (67532, 0.)], maxlen=4096))


def cut_records(error=CUT):
    return [{"kind": "world", "ts_ms": 67433, "payload": {"stamp_ms": 67387, "confidence": 1}},
            {"kind": "reject", "ts_ms": 67497, "payload": {"error": error}},
            {"kind": "stop", "ts_ms": 67497, "payload": {"reason": "Denied"}},
            {"kind": "world", "ts_ms": 67503, "payload": {"stamp_ms": 67499, "confidence": 0}},
            {"kind": "stop", "ts_ms": 67535, "payload": {"reason": "Unarmed"}}]


class CoverageCutFirstTest(unittest.TestCase):
    def classify(self, since_ms=67398, records=None, **change):
        evidence = dict(failing_run(), **change)
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sillok.jsonl"
            log.write_text("\n".join(json.dumps(row) for row in (cut_records() if records is None else records)) + "\n")
            return coverage_cut_first(log, evidence["states"], evidence["outcomes"], evidence["zeros"],
                                      evidence["commands"], evidence["published"], 10.0, since_ms)

    def test_failing_run_is_inconclusive_and_never_a_pass(self):
        # Anchored at the cut's own zero (10.218), which arrived before its reject outcome.
        self.assertEqual(self.classify(), {"zero_at": 10.218, "inconclusive": {
            "cause": "world_authority_cut_before_unknown_world", "cut": CUT,
            "cut_after_fault_wall_ms": 220.0, "unknown_world_after_fault_wall_ms": 235.0,
            "unknown_world_stamp_ms": 67499, "last_world_stamp_ms": 67387, "fault_to_zero_wall_ms": 218.0,
            "fault_sim_ms": 67398, "fault_to_zero_sim_ms": 103}})
        # Without the fault's engine-clock boundary, log order cannot prove its first cause.
        self.assertIsNone(self.classify(since_ms=None))
        # The unchanged coverage oracle has no perception-unknown revocation to accept.
        rows = [{"kind": "world", "ts_ms": 67433, "payload": {"stamp_ms": 67387, "confidence": 1}},
                {"kind": "reject", "ts_ms": 67497, "payload": {"error": CUT}},
                {"kind": "stop", "ts_ms": 67497, "payload": {"reason": "Denied"}},
                {"kind": "world", "ts_ms": 67503, "payload": {"stamp_ms": 67499, "confidence": 0}},
                {"kind": "stop", "ts_ms": 67535, "payload": {"reason": "Unarmed"}}]
        run = failing_run()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sillok.jsonl"
            log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            stop = lambda perception: sensor_stop_report(log, run["states"], run["outcomes"], run["zeros"],
                                                         10.0, 67398, require_perception=perception)
            self.assertIsNone(stop(True))
            # The disconnect oracle would accept the same named, disarmed stop.
            self.assertEqual(stop(False)["stop_reason"], "world_admission_budget_exhausted")

    def test_each_named_world_authority_cut_counts(self):
        for error in WORLD_AUTHORITY_CUTS:
            outcomes = [(t, {"rejected": {"error": error}}) if row.get("rejected", {}).get("error") == CUT else (t, row)
                        for t, row in failing_run()["outcomes"]]
            self.assertEqual(self.classify(outcomes=outcomes, records=cut_records(error))["inconclusive"]["cut"], error)

    def test_earlier_authority_loss_without_a_ros_outcome_is_not_inconclusive(self):
        for reason in ("StaleWorld", "Denied", "Unarmed", "Revoked", "NoWorld", "StateFault"):
            with self.subTest(reason):
                records = cut_records()
                records.insert(1, {"kind": "stop", "ts_ms": 67480, "payload": {"reason": reason}})
                self.assertIsNone(self.classify(records=records))
        # Positive proposal expiry keeps its source armed; the later named cut remains first.
        records = cut_records()
        records.insert(1, {"kind": "stop", "ts_ms": 67480, "payload": {"reason": "Expired"}})
        self.assertIsNotNone(self.classify(records=records))

    def test_automatic_seal_does_not_interrupt_adjacent_engine_reject_and_stop(self):
        records = cut_records()
        seal = {"kind": "seal", "ts_ms": 1_791_687_710_139,
                "payload": {"key_id": "a" * 16, "sig": "b" * 128}}
        # append() may seal immediately after the named reject at its entry-count
        # boundary, before the engine writes Denied using its simulation clock.
        records.insert(2, seal)
        self.assertEqual(self.classify(records=records)["inconclusive"]["cut"], CUT)
        for broken in (
                records[:3] + records[4:],  # The seal cannot replace the actual stop.
                records[:3] + [{"kind": "stop", "ts_ms": 67498, "payload": {"reason": "Denied"}}] + records[4:],
                records[:3] + [{"kind": "world", "ts_ms": 67497,
                               "payload": {"stamp_ms": 67387, "confidence": 1}}] + records[3:],
                records[:2] + [dict(seal, payload=None)] + records[3:]):
            with self.subTest(records=broken):
                self.assertIsNone(self.classify(records=broken))

    def test_incomplete_or_contradictory_engine_sequence_never_allows_retry(self):
        records = cut_records()
        for name, evidence in {
                "missing engine trace": [],
                "missing reject": records[:1] + records[2:],
                "missing Denied stop": records[:2] + records[3:],
                "different rejection": cut_records("source must send a zero command to arm"),
                "tick expiry overrides the named rejection": records[:2] + [
                    {"kind": "stop", "ts_ms": 67497, "payload": {"reason": "StaleWorld"}}] + records[3:],
                "different stop timestamp": records[:2] + [
                    {"kind": "stop", "ts_ms": 67498, "payload": {"reason": "Denied"}}] + records[3:],
                "revocation before the cut": records[:1] + [
                    {"kind": "revoke", "ts_ms": 67480, "payload": {"fired": ["perception-unknown"]}}] + records[1:],
                "bul before the cut": records[:1] + [
                    {"kind": "decision", "ts_ms": 67480, "payload": {"verdict": "bul"}}] + records[1:],
                "unknown world before the cut": records[:1] + [records[3]] + records[1:],
                "different accepted unknown world": records[:3] + [
                    {"kind": "world", "ts_ms": 67503, "payload": {"stamp_ms": 67500, "confidence": 0}}],
                "newly allowed action after cut": records[:3] + [
                    {"kind": "decision", "ts_ms": 67502, "payload": {"verdict": "yun"}}] + records[3:],
        }.items():
            with self.subTest(name):
                self.assertIsNone(self.classify(records=evidence))

    def test_contradictory_callback_stop_is_not_hidden_by_later_cut(self):
        run = failing_run()
        for reason in ("stale_world", "denied", "unarmed"):
            with self.subTest(reason):
                states = [run["states"][0], (10.18, dict(DENIED, stop=reason))] + run["states"][1:]
                self.assertIsNone(self.classify(states=states))

    def test_log_boundary_is_preserved_and_incomplete_evidence_fails_closed(self):
        root = Path("/trusted/run")
        path = root / "gate" / "sillok.jsonl"
        valid = "\n".join(json.dumps(row) for row in cut_records()) + "\n"
        for contents in ("", '{"kind":', valid + '{"kind":', valid + "[]\n", valid + '{}\n'):
            with self.subTest(contents=contents), mock.patch("stop_evidence.read_evidence_text", return_value=contents) as read:
                self.assertFalse(coverage_cut_record(path, 67398, CUT, 67499, root=root, expected_uid=1234))
                read.assert_called_once_with(root, path, 1234)
        with mock.patch("stop_evidence.read_evidence_text", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                coverage_cut_record(path, 67398, CUT, 67499, root=root, expected_uid=1234)

    def test_zero_anchor_follows_the_command_topic_order(self):
        run = failing_run()
        # The cut's zero reached the harness after its reject outcome: still the anchor.
        late = self.classify(zeros=[(10.221, 67502), (10.236, 67503)],
                             commands=[(10.02, .12), (10.21, .12), (10.221, 0.), (10.236, 0.)])
        self.assertEqual((late["zero_at"], late["inconclusive"]["fault_to_zero_sim_ms"]), (10.221, 104))
        # An earlier zero without a rejection (an expired proposal; the source stays armed and a
        # new proposal moves again) does not replace the cut's own zero as the anchor.
        expired = self.classify(zeros=[(10.1, 67450)] + list(run["zeros"]),
                                commands=[(10.02, .12), (10.1, 0.), (10.15, .12)] + run["commands"][1:])
        self.assertEqual(expired["zero_at"], 10.218)

    def test_perception_path_first_is_never_inconclusive(self):
        # Pull request run 38099376514 (048f6f3): the confidence-0 world (stamp 69147) was
        # accepted while proposal 215 was active and revoked in the same step.
        outcomes = [(10.05, {"decision": {"proposal_id": 215, "verdict": "yun"}}),
                    (10.1, {"world_updated": {"stamp_ms": 69147}})]
        states = [(10.05, dict(ARMED)), (10.1, {"stop": "revoked", "armed": [], "active": None}), (10.2, UNARMED)]
        self.assertIsNone(self.classify(outcomes=outcomes + [(10.15, {"rejected": {"error": CUT}})], states=states,
                                        published=[(69147, 0.)]))

    def test_anything_short_of_the_full_evidence_fails(self):
        run = failing_run()
        outcomes, states = run["outcomes"], run["states"]
        unknown_first = [row for row in outcomes if row[1].get("world_updated", {}).get("stamp_ms") != 67499]
        unknown_first.insert(3, (10.22, {"world_updated": {"stamp_ms": 67499}}))
        for name, change in {
                "no confidence-0 world accepted": dict(outcomes=[row for row in outcomes if "world_updated" not in row[1]
                                                                  or row[1]["world_updated"]["stamp_ms"] != 67499]),
                "confidence-0 world accepted before the cut": dict(
                    outcomes=outcomes[:3] + [(10.215, {"world_updated": {"stamp_ms": 67480}})] + outcomes[3:],
                    published=list(run["published"]) + [(67480, 0.)]),
                "same arrival time, unknown world first in outcome order": dict(outcomes=unknown_first),
                "motion command after the zero": dict(commands=run["commands"] + [(10.4, .12)]),
                "re-armed after the cut": dict(states=states + [(10.4, dict(ARMED))]),
                "active proposal after the cut": dict(states=states + [(10.4, dict(UNARMED, active={"id": 214}))]),
                "latest state without an active field": dict(states=states + [(10.4, {"stop": "unarmed", "armed": []})]),
                "latest state not denied or unarmed": dict(states=states + [(10.4, dict(DENIED, stop="expired"))]),
                "no state after the cut": dict(states=states[:2]),
                # The cut's own state left before its outcome: wait for the next state.
                "only the cut's own state, received before its outcome": dict(states=states[:3]),
                "no zero after the cut": dict(zeros=[(10.218, 67501)]),
                "request-age rejection is not a named cut": dict(outcomes=[
                    (t, {"rejected": {"error": "original controller authority has insufficient admission budget"}})
                    if row.get("rejected", {}).get("error") == CUT else (t, row) for t, row in outcomes]),
                "cut before the fault": dict(outcomes=[(9.9, {"rejected": {"error": CUT}})] + [
                    row for row in outcomes if row[1].get("rejected", {}).get("error") != CUT]),
                "unknown stamp also published with confidence 1": dict(published=list(run["published"]) + [(67499, 1.)]),
                "non-integer stamp": dict(outcomes=[(t, {"world_updated": {"stamp_ms": float(row["world_updated"]["stamp_ms"])}})
                                                    if "world_updated" in row else (t, row) for t, row in outcomes]),
                # The cut's own zero arrived first, so a motion command between the reject
                # outcome and the next tick's zero still follows the anchor.
                "motion command after the cut's own zero": dict(
                    commands=sorted(run["commands"] + [(10.225, .12)])),
                "request-age rejection before the named cut": dict(outcomes=outcomes[:3] + [
                    (10.215, {"rejected": {"error": "original controller authority has insufficient admission budget"}})]
                    + outcomes[3:]),
                "bul decision before the cut": dict(outcomes=outcomes[:3] + [
                    (10.215, {"decision": {"proposal_id": 214, "verdict": "bul"}})] + outcomes[3:]),
                "stamp published with confidence 0 and 1, accepted before the cut": dict(
                    outcomes=outcomes[:3] + [(10.215, {"world_updated": {"stamp_ms": 67470}})] + outcomes[3:],
                    published=list(run["published"]) + [(67470, 1.), (67470, 0.)]),
                "stamp missing from the published record, accepted before the cut": dict(
                    outcomes=outcomes[:3] + [(10.215, {"world_updated": {"stamp_ms": 67470}})] + outcomes[3:]),
                # The latest state must have nothing armed and a denied or unarmed stop.
                "source still armed": dict(states=states + [(10.4, dict(UNARMED, armed=["vla"]))]),
                "armed field missing": dict(states=states + [(10.4, {"stop": "unarmed", "active": None})]),
                "revoked latest state": dict(states=states + [(10.4, dict(DENIED, stop="revoked"))]),
                # No cut zero, and motion commands kept arriving after the rejection: the
                # first zero after them must not hide them.
                "motion for 300 ms after the rejection, then zeros": dict(
                    commands=run["commands"][:2] + [(10.22 + .02 * i, .12) for i in range(1, 16)]
                    + [(10.55, 0.), (10.6, 0.)], zeros=[(10.55, 67650), (10.6, 67672)]),
                "motion for 2 s after the rejection, then a zero": dict(
                    commands=run["commands"][:2] + [(10.22 + .02 * i, .12) for i in range(1, 100)]
                    + [(12.3, 0.)], zeros=[(12.3, 68500)]),
        }.items():
            with self.subTest(name):
                self.assertIsNone(self.classify(**change))


class MotionAfterStopTest(unittest.TestCase):
    """The order rule that keeps "no motion after the stop" blocking without a timing limit."""

    def test_motion_after_the_first_disarmed_state_is_returned(self):
        run = failing_run()
        # Main run 38106819587: the cut's zero, then its denied state (10.219); only zeros followed.
        self.assertEqual(motion_after_stop(run["states"], run["commands"], 10.0), [])
        late = [(10.22 + .02 * i, .12) for i in range(1, 100)] + [(12.3, 0.)]
        for name, states in (
                ("denied", run["states"]),
                ("revoked", [(10.02, dict(ARMED)), (10.219, dict(DENIED, stop="revoked"))]),
                ("stale world", [(10.02, dict(ARMED)), (10.219, dict(DENIED, stop="stale_world"))]),
                ("unarmed", [(10.02, dict(ARMED)), (10.219, dict(UNARMED))]),
                ("armed field missing", [(10.219, {"stop": "denied"})])):
            with self.subTest(name):
                # A later zero cannot erase any motion command received after the stop.
                self.assertEqual(motion_after_stop(states, run["commands"][:2] + late, 10.0), late[:-1])

    def test_even_one_post_stop_motion_command_is_not_exempt(self):
        states = [(10.02, dict(ARMED)), (10.219, DENIED)]
        moving = [(10.02, .12), (10.21, .12)]
        # The receive history cannot prove which command was already in the relay.
        self.assertEqual(motion_after_stop(states, moving + [(10.22, .12), (10.23, 0.)], 10.0), [(10.22, .12)])
        self.assertEqual(motion_after_stop(states, moving + [(10.22, .12), (10.23, .12)], 10.0),
                         [(10.22, .12), (10.23, .12)])
        self.assertEqual(motion_after_stop(states, moving + [(10.22, .12), (10.23, 0.), (10.24, .12)], 10.0),
                         [(10.22, .12), (10.24, .12)])
        # A preceding zero also gives no exemption.
        self.assertEqual(motion_after_stop(states, moving + [(10.218, 0.), (10.225, .12)], 10.0), [(10.225, .12)])
        # Equal timestamps cannot establish that a command preceded the state.
        self.assertEqual(motion_after_stop(states, moving + [(10.219, 0.), (10.22, .12)], 10.0), [(10.22, .12)])
        self.assertEqual(motion_after_stop(states, moving + [(10.219, .12), (10.22, 0.)], 10.0), [(10.219, .12)])

    def test_motion_before_the_stop_or_while_armed_is_allowed(self):
        commands = [(10.02, .12), (10.21, .12), (10.218, 0.), (10.3, -.02)]
        self.assertEqual(motion_after_stop([(10.219, DENIED)], commands, 10.0), [])
        # No stop yet, or an expired proposal with the source still armed: a new proposal may move.
        expired = [(10.1, {"stop": "expired", "armed": ["vla"], "active": None}), (10.15, dict(ARMED))]
        for states in ([], [(10.02, dict(ARMED))], expired):
            self.assertEqual(motion_after_stop(states, commands + [(10.5, .12), (10.6, .12)], 10.0), [])
        # A disarmed state from before the fault does not count.
        self.assertEqual(motion_after_stop([(9.9, DENIED)], commands + [(10.5, .12), (10.6, .12)], 10.0), [])


class CoverageAttemptTest(unittest.TestCase):
    """Execute the actual exercise_sensor_fault() against a scripted world, without ROS."""

    def run_case(self, perception_revokes=False, late_motion=False, motion_after_fault=0, recovery=None,
                 earlier_stop=None):
        from permit_attempts import RunnerDelay
        tree = ast.parse(Path(__file__).with_name("run_reference.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "exercise_sensor_fault")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        log = Path(directory.name) / "sillok.jsonl"
        records = cut_records()
        if earlier_stop:
            records.insert(1, {"kind": "stop", "ts_ms": 67480, "payload": {"reason": earlier_stop}})
        log.write_text("\n".join(json.dumps(row) for row in records) + "\n")
        now = [10.0]
        def clock():
            now[0] += .001
            return now[0]
        def wait_for(predicate, timeout, processes, description, action=None):
            for _ in range(50):
                if action:
                    action()
                if predicate():
                    return clock()
            raise TimeoutError(description)
        world = SimpleNamespace(sensor_info={"healthy": True, "stamp_ms": 67387}, states=[], outcomes=[], commands=[],
                                zero_commands=deque(maxlen=512), published_worlds=deque(maxlen=4096), moving=False,
                                perception=SimpleNamespace(snapshot=lambda *_: ([], 1., {})), pose=lambda: (5.75, 5.),
                                get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=67_398_000_000)),
                                controllers_unlocked=lambda: True, marker=lambda *a, **k: None, emitted=[])
        world._emit = lambda kind, **values: world.emitted.append(kind)
        world.speed = lambda: .08 if world.moving else 0.
        def command(linear):
            world.commands.append((clock(), linear))
            if linear == 0:
                world.zero_commands.append((now[0], 67500))
        def propose_base(linear):
            if linear == 0:
                world.states.append((clock(), {"stop": "no_command", "armed": ["vla"], "active": None}))
                world.outcomes.extend([(clock(), {"decision": {"verdict": "yun", "action": {"type": "stop"}}})] * 2)
                command(0.)
            elif world.states and world.states[-1][1]["armed"]:
                world.moving = True
                world.states.append((clock(), dict(ARMED)))
                command(.12)
            else:  # A disarmed source: Rust rejects and the gate publishes zero.
                world.outcomes.append((clock(), {"rejected": {"error": "source must send a zero command to arm"}}))
                world.states.append((clock(), dict(UNARMED)))
                command(.12 if late_motion and world.sensor_info["healthy"] else 0.)
                if recovery and world.sensor_info["healthy"]:  # A defective recovery.
                    if recovery == "moves":
                        world.moving = True
                    else:
                        world.states.append((clock(), dict(ARMED, active=None)))
        def calibration_visible(visible):
            world.sensor_info["healthy"] = visible
            if not visible:
                world.moving = False
                world.published_worlds.append((67499, 0.))
                if not perception_revokes:
                    if not motion_after_fault:
                        command(0.)  # The cut's own zero, before its reject outcome.
                    world.outcomes.append((clock(), {"rejected": {"error": CUT}}))
                    world.states.append((clock(), DENIED))
                world.outcomes.append((clock(), {"world_updated": {"stamp_ms": 67499}}))
                world.states.append((clock(), {"stop": "revoked", "armed": [], "active": None}
                                     if perception_revokes else DENIED))
                for _ in range(motion_after_fault):  # A defective gate keeps moving after its stop.
                    command(.12)
                command(0.)
            return True
        world.propose_base = propose_base
        world.native = SimpleNamespace(calibration_visible=calibration_visible)
        stop_report = lambda states, outcomes, zeros, since, since_ms, require_perception: (
            {"stop_reason": "perception_unknown", "zero_at": zeros[-1][0], "world_stamp_ms": 67499}
            if perception_revokes and zeros and zeros[-1][0] >= since else None)
        scope = {"time": SimpleNamespace(monotonic=clock, sleep=lambda seconds: None), "json": json,
                 "wait_for": wait_for, "coverage_cut_first": coverage_cut_first, "RunnerDelay": RunnerDelay,
                 "motion_after_stop": motion_after_stop}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "run_reference.py", "exec"), scope)
        return world, lambda: scope["exercise_sensor_fault"](
            world, {}, "coverage", stop_report, cut_report=partial(coverage_cut_first, log))

    def test_authority_cut_is_inconclusive_only_after_recovery_without_rearm(self):
        from permit_attempts import RunnerDelay
        world, run = self.run_case()
        with self.assertRaises(RunnerDelay) as raised:
            run()
        self.assertEqual((raised.exception.evidence["cause"], raised.exception.evidence["cut"]),
                         ("world_authority_cut_before_unknown_world", CUT))
        # The fault's simulation stamp reached the classifier: both clocks are recorded.
        self.assertEqual((raised.exception.evidence["fault_sim_ms"], raised.exception.evidence["fault_to_zero_sim_ms"]),
                         (67398, 102))
        # The target was restored and a recovery proposal was rejected before the retry.
        self.assertTrue(world.sensor_info["healthy"])
        self.assertEqual(world.outcomes[-1][1], {"rejected": {"error": "source must send a zero command to arm"}})
        self.assertNotIn("sensor_fault_result", world.emitted)

    def test_motion_after_the_stop_fails_instead_of_retrying(self):
        # Motion commands after recovery, on proposals the engine rejected.
        _, run = self.run_case(late_motion=True)
        with self.assertRaisesRegex(AssertionError, "sensor fault coverage motion command after the stop"):
            run()

    def test_motion_after_the_named_stop_fails_and_is_never_retried(self):
        from permit_attempts import AttemptList, bounded_attempts
        for count in (1, 5):
            with self.subTest(motion_commands=count):
                # Even one motion command after the stop, followed by a zero, must
                # fail inside real bounded_attempts() without retrying or passing.
                _, run = self.run_case(motion_after_fault=count)
                attempts = AttemptList()
                with self.assertRaisesRegex(TimeoutError, "sensor fault causes authenticated engine stop"):
                    bounded_attempts("sensor_coverage", run, attempts, clock=lambda: 0.0, label="sensor coverage")
                self.assertEqual([(row["attempt"], row["outcome"]) for row in attempts], [(1, "failed")])
                world, run = self.run_case(perception_revokes=True, motion_after_fault=count)
                with self.assertRaisesRegex(AssertionError, "sensor fault coverage motion command after the stop"):
                    run()
                self.assertNotIn("sensor_fault_result", world.emitted)

    def test_recovery_that_rearms_or_moves_fails_before_any_retry(self):
        for recovery in ("rearms", "moves"):
            with self.subTest(recovery):
                _, run = self.run_case(recovery=recovery)
                with self.assertRaisesRegex(AssertionError, "sensor recovery automatically rearmed motion"):
                    run()

    def test_earlier_tick_stop_is_not_retried_as_the_later_named_cut(self):
        from permit_attempts import AttemptList, bounded_attempts
        for reason in ("StaleWorld", "Denied", "Unarmed"):
            with self.subTest(reason):
                _, run = self.run_case(earlier_stop=reason)
                attempts = AttemptList()
                with self.assertRaisesRegex(TimeoutError, "sensor fault causes authenticated engine stop"):
                    bounded_attempts("sensor_coverage", run, attempts, clock=lambda: 0.0, label="sensor coverage")
                self.assertEqual([(row["attempt"], row["outcome"]) for row in attempts], [(1, "failed")])

    def test_perception_revocation_still_passes(self):
        world, run = self.run_case(perception_revokes=True)
        result = run()
        self.assertEqual((result["stop_reason"], result["ok"]), ("perception_unknown", True))
        self.assertIn("sensor_fault_result", world.emitted)


if __name__ == "__main__":
    unittest.main()
