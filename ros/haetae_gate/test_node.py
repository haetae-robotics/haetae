"""Transport control-flow tests using actual node methods without ROS installed."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from bridge import (BridgeFailure, StaleActuation, ExpiredActuation,
                    require_fresh_actuation, lease_renewable, reject_expired_actuation)
from proposals import InvalidProposal

tree = ast.parse(Path(__file__).with_name('node.py').read_text())
definition = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'HaetaeGate')
definition.bases = []
clock = SimpleNamespace(value=0.0)
environment = dict(time=SimpleNamespace(monotonic=lambda: clock.value,
                                       monotonic_ns=lambda: int(clock.value * 1_000_000_000)), UInt64=SimpleNamespace,
                   String=SimpleNamespace, IDLE="0" * 64,
                   GoalStatus=SimpleNamespace(STATUS_SUCCEEDED=4, STATUS_CANCELED=5, STATUS_ABORTED=6),
                   BridgeFailure=BridgeFailure, StaleActuation=StaleActuation,
                   ExpiredActuation=ExpiredActuation, InvalidProposal=InvalidProposal,
                   require_fresh_actuation=require_fresh_actuation, lease_renewable=lease_renewable,
                   reject_expired_actuation=reject_expired_actuation)
exec(compile(ast.Module(body=[definition], type_ignores=[]), 'actual-node-methods', 'exec'), environment)
Gate = environment['HaetaeGate']


class NodeBoundaryTest(unittest.TestCase):
    def setUp(self):
        clock.value = 0
        g = self.gate = Gate.__new__(Gate)
        g.failed = False
        g.signer = None
        g.permits = None
        g.arm_goal = None
        g.arm_goal_future = object()
        g.arm_goal_deadline = None
        g.arm_cancel_future = None
        g.arm_cancel_deadline = None
        g.cancel_requested = False
        g.world_max_age_ms = 200
        g.permit_world_remaining_ns = 200_000_000
        g.max_actuation_response_ms = 50
        g.command_pub = SimpleNamespace(topic_name='/resolved/base/cmd_vel')
        g.count_publishers = lambda topic: 1
        g._now = lambda: 1000 + int(clock.value * 1000)
        g.step = {'cmd': {'linear': 0, 'angular': 0}, 'status': {
            'mode': 'normal', 'world_age_ms': 0, 'recorder_ok': True, 'state_ok': True,
            'armed': [], 'active': None, 'arm_cancelling': False}}
        g.requests, g.heartbeats, g.aborts = [], [], []
        def request(value):
            g.requests.append(value)
            return g.step
        g.bridge = SimpleNamespace(request=request)
        g.heartbeat_pub = SimpleNamespace(publish=lambda message: g.heartbeats.append(message.data))
        g._publish = lambda step: None
        g._abort = lambda exc: g.aborts.append(str(exc))

    def test_delayed_cancel_is_fresh_revocation_without_restamping_authority(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(challenges={"arm": "b" * 32},
                                    sign=lambda *args: signed.append(args) or "token")
        g.permit_sim_ns, g.permit_wall_ns, g.permit_remaining_ns = 1_000_000_000, 0, 100_000_000
        clock.value = .25
        g._cancel_arm()
        self.assertEqual(signed[-1], ("arm", "stop", "0" * 64, 1_250_000_000,
                                      200_000_000, 250_000_000))
        for kind in ("command", "goal", "lease", "reset"):
            with self.assertRaises(ExpiredActuation):
                g._permit("arm", kind, "a" * 64)
            clock.value = .02
            g._permit("arm", kind, "a" * 64)
            self.assertEqual(signed[-1][3:], (1_000_000_000, 100_000_000, 0))
            clock.value = .25
        with self.assertRaises(ValueError):
            g._permit("arm", "stop", "0" * 64)

    def test_short_original_grant_is_not_dispatched_near_expiry(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(sign=lambda *args: signed.append(args) or "token")
        g.permit_sim_ns, g.permit_wall_ns = 1_000_000_000, 0
        clock.value = .006
        g.permit_remaining_ns = 56_000_000
        for kind in ("goal", "lease", "reset", "command"):
            with self.assertRaises(ExpiredActuation):
                g._permit("arm", kind, "a" * 64)
        self.assertFalse(signed)
        g.permit_remaining_ns += 1
        g._permit("arm", "lease", "a" * 64)
        self.assertEqual(signed[-1][3:], (1_000_000_000, 56_000_001, 0))

    def test_world_admission_cutoff_is_distinct_from_proposal_expiry(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(sign=lambda *args: signed.append(args) or "token")
        g.permit_sim_ns, g.permit_wall_ns = 1_000_000_000, 0
        clock.value = .006
        g.permit_remaining_ns = g.permit_world_remaining_ns = 56_000_000
        with self.assertRaisesRegex(ExpiredActuation, "trusted world has insufficient"):
            g._permit("base", "command", "a" * 64)
        self.assertFalse(signed)
        g.permit_world_remaining_ns = 200_000_000
        with self.assertRaisesRegex(ExpiredActuation, "original controller authority"):
            g._permit("base", "command", "a" * 64)
        self.assertFalse(signed)
        g.permit_remaining_ns = g.permit_world_remaining_ns = 56_000_001
        g._permit("base", "command", "a" * 64)
        self.assertEqual(signed[-1][3:], (1_000_000_000, 56_000_001, 0))

    def test_relay_ack_cannot_renew_before_trusted_controller_admission(self):
        g = self.gate
        g.arm_goal_future = None  # The untrusted relay already acknowledged.
        digest = "a" * 64
        g.permits = SimpleNamespace(challenges={"arm": "b" * 32},
                                    active_arm=digest, admitted_arm="0" * 64,
                                    admitted_arm_holding=False,
                                    goal_sequence=3, admitted_goal_sequence=0)
        g.permit_reset = False
        g.permit_stop = False
        g._permit = lambda target, kind, payload: kind + ":" + payload
        g._heartbeat(1000)
        self.assertFalse(g.heartbeats)
        g.permits.admitted_arm = digest
        g._heartbeat(1000)
        self.assertFalse(g.heartbeats)  # Identical previous goal is insufficient.
        g.permits.admitted_goal_sequence = 3
        g._heartbeat(1000)
        self.assertEqual(g.heartbeats, ["lease:" + digest])
        g.permit_reset = True
        g.permits.admitted_arm = "c" * 64
        g._heartbeat(1000)
        self.assertEqual(g.heartbeats[-1], "reset:" + "0" * 64)
        self.assertEqual(g.permits.active_arm, "0" * 64)
        self.assertEqual(g.permits.goal_sequence, 0)
        g.permit_reset = False
        count = len(g.heartbeats)
        g._heartbeat(1000)
        self.assertEqual(len(g.heartbeats), count)  # Old digest/sequence telemetry.
        g.permits.admitted_arm = "0" * 64
        g.permits.admitted_goal_sequence = 0
        g._heartbeat(1000)
        self.assertEqual(g.heartbeats[-1], "lease:" + "0" * 64)
        g.permits.admitted_arm_holding = True
        count = len(g.heartbeats)
        g._heartbeat(1000)
        self.assertEqual(len(g.heartbeats), count)  # Locked/holding IDLE cannot renew.
        g.permit_reset = True
        g._heartbeat(1000)
        self.assertEqual(g.heartbeats[-1], "reset:" + "0" * 64)
        g.permit_reset = False
        g.permit_stop = True
        count = len(g.heartbeats)
        g._heartbeat(1000)
        self.assertEqual(len(g.heartbeats), count)  # Disarmed owner cannot renew.

    def test_success_retains_admitted_digest_until_explicit_stop(self):
        g = self.gate
        g.permits = SimpleNamespace(active_arm="a" * 64)
        g._on_arm_result(SimpleNamespace(result=lambda: SimpleNamespace(status=4)))
        self.assertFalse(g.aborts)
        self.assertEqual(g.permits.active_arm, "a" * 64)

    def test_completion_winning_signed_stop_race_cannot_restore_authority(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(challenges={"arm": "b" * 32},
                                    active_arm="a" * 64, goal_sequence=3,
                                    sign=lambda *args: signed.append(args) or "stop-token")
        g.arm_goal = SimpleNamespace(cancel_goal_async=lambda: SimpleNamespace(add_done_callback=lambda _: None))
        g.arm_goal_future = None
        g._cancel_arm()
        self.assertTrue(g.cancel_requested)
        self.assertEqual(signed[-1][1], "stop")
        g._on_arm_result(SimpleNamespace(result=lambda: SimpleNamespace(status=4)))
        self.assertFalse(g.aborts)
        self.assertIsNone(g.arm_goal)
        self.assertEqual(g.permits.active_arm, "0" * 64)
        self.assertEqual(g.permits.goal_sequence, 0)
        g.permits = None
        g.cancel_requested = True
        g._on_arm_result(SimpleNamespace(result=lambda: SimpleNamespace(status=4)))
        self.assertEqual(g.aborts, ['arm did not report a cancelled result'])

    def test_pending_goal_cancellation_has_absolute_deadline_and_no_renewal(self):
        g = self.gate
        g._cancel_arm()
        self.assertEqual(g.arm_cancel_deadline, .25)
        clock.value = .1
        g._cancel_arm()
        g._tick()
        self.assertEqual(g.arm_cancel_deadline, .25)
        self.assertFalse(g.heartbeats)
        clock.value = .25
        g._tick()
        self.assertEqual(g.aborts, ['arm cancellation did not complete in 250 ms'])

    def test_acceptance_timeout_and_late_accepted_handle_are_stopped(self):
        g = self.gate
        g.arm_goal_deadline = .25
        clock.value = .25
        g._tick()
        self.assertEqual(g.aborts, ['arm goal acceptance did not complete in 250 ms'])
        cancelled = []
        handle = SimpleNamespace(accepted=True, cancel_goal_async=lambda: cancelled.append(True))
        g.failed = True
        g._on_arm_goal(SimpleNamespace(result=lambda: handle))
        self.assertEqual(cancelled, [True])
        self.assertFalse(g.heartbeats)

    def test_signed_stop_can_overtake_pending_goal_without_restoring_authority(self):
        g = self.gate
        g.permits = SimpleNamespace(challenges={"arm": "b" * 32}, active_arm="a" * 64,
                                    goal_sequence=1, sign=lambda *args: "signed-stop")
        g._cancel_arm()
        g._on_arm_goal(SimpleNamespace(result=lambda: SimpleNamespace(accepted=False)))
        self.assertFalse(g.aborts)
        self.assertIsNone(g.arm_goal)
        self.assertIsNone(g.arm_goal_future)
        self.assertIsNone(g.arm_goal_deadline)
        self.assertIsNone(g.arm_cancel_deadline)
        self.assertFalse(g.cancel_requested)
        self.assertEqual(g.permits.active_arm, "0" * 64)
        self.assertEqual(g.permits.goal_sequence, 0)
        self.assertEqual(g.heartbeats, ["signed-stop"])
        for permit, cancelling in ((None, True), (g.permits, False)):
            g.aborts.clear()
            g.permits, g.cancel_requested = permit, cancelling
            g._on_arm_goal(SimpleNamespace(result=lambda: SimpleNamespace(accepted=False)))
            self.assertEqual(g.aborts, ["arm goal rejected"])

    def test_publish_failure_cannot_renew_lease_and_healthy_control_can(self):
        g = self.gate
        g.arm_goal_future = None
        g._receive(lambda: g.step)
        self.assertEqual(g.heartbeats, [1000])
        g.heartbeats.clear()
        def fail(step):
            raise BridgeFailure('publish failed')
        g._publish = fail
        g._receive(lambda: g.step)
        self.assertFalse(g.heartbeats)
        self.assertEqual(g.aborts, ['publish failed'])

    def test_engine_settling_suppresses_lease_and_resolved_topic_raises_hold(self):
        g = self.gate
        g.step['status']['arm_cancelling'] = True
        g._tick()
        self.assertFalse(g.heartbeats)
        topics = []
        g.count_publishers = lambda topic: topics.append(topic) or 2
        g._tick()
        self.assertEqual(topics, ['/resolved/base/cmd_vel'])
        self.assertEqual(g.requests[-1], {'k': 'hold', 't': 1000})
