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
environment = dict(time=SimpleNamespace(monotonic=lambda: clock.value), UInt64=SimpleNamespace,
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
        g.arm_goal = None
        g.arm_goal_future = object()
        g.arm_goal_deadline = None
        g.arm_cancel_future = None
        g.arm_cancel_deadline = None
        g.cancel_requested = False
        g.world_max_age_ms = 200
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
