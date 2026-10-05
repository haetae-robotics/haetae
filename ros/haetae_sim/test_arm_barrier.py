import copy
import unittest
from types import SimpleNamespace

from arm_barrier import rearm_ready, gazebo_rearm_ready


class ArmBarrierTest(unittest.TestCase):
    def test_requires_latest_accepted_stop_and_newer_fresh_idle_state(self):
        state = {"mode": "normal", "armed": ["vla"], "active": None,
                 "arm_cancelling": False, "arm_controller_ready": True,
                 "recorder_ok": True, "state_ok": True, "world_age_ms": 20}
        stop = {"decision": {"verdict": "yun", "action": {"type": "stop"}}}
        self.assertTrue(rearm_ready([(3, state)], [(2, stop)], 1))
        self.assertFalse(rearm_ready([(2, state)], [(2, stop)], 1))
        self.assertFalse(rearm_ready([(3, state)], [(2, stop)], 2.1))
        for delta in ({"mode": "hold"}, {"armed": []}, {"active": {"id": 1}},
                      {"arm_cancelling": True}, {"arm_controller_ready": False},
                      {"recorder_ok": False}, {"state_ok": False},
                      {"world_age_ms": 75}, {"world_age_ms": None}):
            changed = copy.deepcopy(state)
            changed.update(delta)
            self.assertFalse(rearm_ready([(3, changed)], [(2, stop)], 1))
        for later in ({"rejected": {}}, {"decision": {"verdict": "bul", "action": {"type": "stop"}}},
                      {"decision": {"verdict": "yun", "action": {"type": "joint_trajectory"}}}):
            self.assertFalse(rearm_ready([(4, state)], [(2, stop), (3, later)], 1))
        self.assertTrue(rearm_ready([(4, state)], [(2, stop), (3, {"world_updated": {}})], 1))

    def fixture(self):
        state = {"mode": "normal", "armed": ["vla"], "active": None,
                 "arm_cancelling": False, "arm_controller_ready": True,
                 "recorder_ok": True, "state_ok": True, "world_age_ms": 20}
        stop = {"decision": {"verdict": "yun", "action": {"type": "stop"}}}
        guard = {"holding": False, "published_wall_ns": 9_960_000_000,
                 "lease_received_wall_ns": 9_940_000_000, "nonce": "current",
                 "active_digest": "0" * 64, "goal_sequence": 0}
        return SimpleNamespace(states=[(9.97, state)], outcomes=[(9.95, stop)],
            guard_states=[(9.98, dict(guard))], base_guard_states=[(9.98, dict(guard))],
            joint=SimpleNamespace(name=["j1", "j2", "j3", "j4"], velocity=[0.] * 4),
            joint_received=9.98, odom_received=9.98, speed=lambda: 0.)

    def ready(self, world, sent=9.93):
        return gazebo_rearm_ready(world, sent, 10., ["j1", "j2", "j3", "j4"],
                                  {"arm": "current", "base": "current"})

    def test_gazebo_reset_needs_current_stop_state_and_both_controller_reports(self):
        world = self.fixture()
        self.assertTrue(self.ready(world))
        self.assertFalse(self.ready(world, sent=9.96))
        for field in ("guard_states", "base_guard_states"):
            for changes in ({"holding": True}, {"nonce": "old"},
                            {"published_wall_ns": 9_890_000_000},
                            {"published_wall_ns": 10_010_000_000}):
                changed = copy.deepcopy(world)
                getattr(changed, field)[-1][1].update(changes)
                self.assertFalse(self.ready(changed))
            for stamp in (9.89, 10.01):
                changed = copy.deepcopy(world)
                setattr(changed, field, [(stamp, getattr(changed, field)[-1][1])])
                self.assertFalse(self.ready(changed))
            changed = copy.deepcopy(world)
            setattr(changed, field, [])
            self.assertFalse(self.ready(changed))
        for lease in (0, 9_920_000_000, 10_010_000_000):
            changed = copy.deepcopy(world)
            changed.guard_states[-1][1]["lease_received_wall_ns"] = lease
            self.assertFalse(self.ready(changed))
        for changes in ({"active_digest": "1" * 64}, {"goal_sequence": 1}):
            changed = copy.deepcopy(world)
            changed.guard_states[-1][1].update(changes)
            self.assertFalse(self.ready(changed))
        world.states[-1] = (9.89, world.states[-1][1])
        self.assertFalse(self.ready(world))

    def test_gazebo_reset_needs_fresh_measured_wheel_and_every_joint_stop(self):
        for field in ("joint_received", "odom_received"):
            for stamp in (9.89, 10.01):
                world = self.fixture()
                setattr(world, field, stamp)
                self.assertFalse(self.ready(world))
        for speed in (0.03, -0.03, float("nan"), float("inf")):
            world = self.fixture()
            world.speed = lambda: speed
            self.assertFalse(self.ready(world))
        for values in ([0., 0., 0.03, 0.], [0.] * 3, [0., float("nan"), 0., 0.]):
            world = self.fixture()
            world.joint.velocity = values
            self.assertFalse(self.ready(world))
        world = self.fixture()
        world.joint.name = world.joint.name[:-1]
        self.assertFalse(self.ready(world))
        world.joint = None
        self.assertFalse(self.ready(world))
