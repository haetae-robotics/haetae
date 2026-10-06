"""Transport control-flow tests using actual node methods without ROS installed."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from bridge import (BridgeFailure, StaleActuation, ExpiredActuation,
                    require_fresh_actuation, lease_renewable, reject_expired_actuation)
from proposals import InvalidProposal
from controller_permits import explicit_rearm

tree = ast.parse(Path(__file__).with_name('node.py').read_text())
definition = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'HaetaeGate')
definition.bases = []
clock = SimpleNamespace(value=0.0)
environment = dict(json=json, time=SimpleNamespace(monotonic=lambda: clock.value,
                                       monotonic_ns=lambda: int(clock.value * 1_000_000_000)), UInt64=SimpleNamespace,
                   String=SimpleNamespace, IDLE="0" * 64,
                   GoalStatus=SimpleNamespace(STATUS_SUCCEEDED=4, STATUS_CANCELED=5, STATUS_ABORTED=6),
                   BridgeFailure=BridgeFailure, StaleActuation=StaleActuation,
                   ExpiredActuation=ExpiredActuation, InvalidProposal=InvalidProposal,
                   require_fresh_actuation=require_fresh_actuation, lease_renewable=lease_renewable,
                   reject_expired_actuation=reject_expired_actuation, explicit_rearm=explicit_rearm)
exec(compile(ast.Module(body=[definition], type_ignores=[]), 'actual-node-methods', 'exec'), environment)
Gate = environment['HaetaeGate']


class NodeBoundaryTest(unittest.TestCase):
    def setUp(self):
        clock.value = 0
        g = self.gate = Gate.__new__(Gate)
        g.failed = False
        g.signed_vla_writers_matched = 0
        g.signed_vla_callbacks = 0
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

    def test_vla_transport_matching_never_sends_authority(self):
        g = self.gate
        for count, expected in ((1, 1), (0, 0), (-1, 0), (True, 0), ('1', 0), (1.0, 0)):
            g._vla_matched(SimpleNamespace(current_count=count))
            self.assertEqual(g.signed_vla_writers_matched, expected)
            self.assertFalse(g.requests)
            self.assertFalse(g.heartbeats)
        self.assertEqual(g.signed_vla_callbacks, 0)

    def test_vla_ingress_diagnostic_counts_rejected_input_without_grant(self):
        g = self.gate
        g.get_logger = lambda: SimpleNamespace(warning=lambda message: None)
        g._signed(SimpleNamespace(data='{}'), 'vla')
        self.assertEqual(g.signed_vla_callbacks, 1)
        self.assertEqual([row['k'] for row in g.requests], ['reject'])
        self.assertFalse(g.heartbeats)
        self.assertFalse(g.aborts)

    def test_delayed_cancel_is_fresh_revocation_without_restamping_authority(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(challenges={"arm": "b" * 32}, sim_backdate_ns=0,
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
        g.permits = SimpleNamespace(sim_backdate_ns=0, sign=lambda *args: signed.append(args) or "token")
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

    def retiring_arm(self):
        g = self.gate
        g.arm_goal_future = None
        g.permits = SimpleNamespace(sim_backdate_ns=0, challenges={"arm": "b" * 32},
            active_arm="a" * 64, admitted_arm="a" * 64, admitted_arm_holding=False,
            goal_sequence=3, admitted_goal_sequence=3)
        g.permit_reset = g.permit_stop = False
        g.permit_sim_ns, g.permit_wall_ns = 1_000_000_000, 0
        g.permit_remaining_ns = 56_000_000
        g.step['status'].update(armed=['vla'], active={"action": {"type": "joint_trajectory"}},
                                active_expires_ms=1056)
        clock.value = .006
        return g

    def test_admitted_arm_retirement_mints_nothing_and_preserves_engine_expiry(self):
        g = self.retiring_arm()
        self.assertTrue(g._arm_renewal_retiring(g.step))
        commands, states = [], []
        g._publish_command = lambda *args: commands.append(args)
        g.state_pub = SimpleNamespace(publish=lambda msg: states.append(json.loads(msg.data)))
        g.outcome_pub = g.decision_pub = SimpleNamespace(publish=lambda msg: None)
        g.arm_joints = []
        g._publish = lambda step: Gate._publish(g, step)
        g.permit_world = SimpleNamespace(observe=lambda *args: None,
                                        remaining=lambda *args: 200_000_000)
        g._permit = lambda *args: self.fail('retirement must mint no positive permit')
        g._receive(lambda: g.step)
        self.assertFalse(commands)
        self.assertFalse(g.heartbeats)
        self.assertFalse(g.requests)
        self.assertFalse(g.aborts)
        self.assertEqual(states[-1]['active_expires_ms'], 1056)
        self.assertEqual(states[-1]['active'], g.step['status']['active'])

    def test_retirement_cannot_hide_new_commands_staleness_or_controller_loss(self):
        g = self.retiring_arm()
        self.assertTrue(g._arm_renewal_retiring(g.step))
        for field, value in (('permit_reset', True), ('permit_stop', True),
                             ('arm_goal_future', object()), ('permit_world_remaining_ns', 56_000_000),
                             ('permit_remaining_ns', 56_000_001), ('permit_remaining_ns', 6_000_000)):
            old = getattr(g, field)
            setattr(g, field, value)
            self.assertFalse(g._arm_renewal_retiring(g.step), field)
            setattr(g, field, old)
        for field, value in (('admitted_arm_holding', True), ('admitted_arm', 'c' * 64),
                             ('admitted_goal_sequence', 2), ('active_arm', '0' * 64)):
            old = getattr(g.permits, field)
            setattr(g.permits, field, value)
            self.assertFalse(g._arm_renewal_retiring(g.step), field)
            setattr(g.permits, field, old)
        for arm in ('cancel', {'execute': {'points': []}}):
            self.assertFalse(g._arm_renewal_retiring({**g.step, 'arm': arm}))
        self.assertFalse(g._arm_renewal_retiring({**g.step, 'cmd': {'linear': .1, 'angular': 0}}))
        for when in (-.001, .05):
            clock.value = when
            self.assertFalse(g._arm_renewal_retiring(g.step))

    def test_world_admission_cutoff_is_distinct_from_proposal_expiry(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(sim_backdate_ns=0, sign=lambda *args: signed.append(args) or "token")
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

    def test_conservative_backdate_rejects_at_earlier_admission_and_world_cutoff(self):
        g = self.gate
        signed = []
        g.permits = SimpleNamespace(sim_backdate_ns=10_000_000,
                                    sign=lambda *args: signed.append(args) or "token")
        g.permit_sim_ns, g.permit_wall_ns = 1_000_000_000, 0
        g.permit_remaining_ns = g.permit_world_remaining_ns = 200_000_000
        clock.value = .04
        with self.assertRaises(ExpiredActuation):
            g._permit("arm", "lease", "a" * 64)
        self.assertFalse(signed)
        clock.value = .039
        g._permit("arm", "lease", "a" * 64)
        self.assertEqual(signed[-1][3:], (1_000_000_000, 200_000_000, 0))
        g.permit_world_remaining_ns = 99_000_000
        with self.assertRaisesRegex(ExpiredActuation, "trusted world has insufficient"):
            g._permit("arm", "lease", "a" * 64)

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
