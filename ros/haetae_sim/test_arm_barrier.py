import copy
import ast
import json
from pathlib import Path
import unittest
from types import SimpleNamespace

from arm_barrier import rearm_ready, gazebo_rearm_ready, activated_guards_ready


class ArmBarrierTest(unittest.TestCase):
    def test_requires_latest_accepted_stop_and_newer_fresh_idle_state(self):
        state = {"mode": "normal", "armed": ["vla"], "active": None,
                 "arm_cancelling": False, "arm_controller_ready": True,
                 "recorder_ok": True, "state_ok": True, "world_age_ms": 20}
        stop = {"decision": {"verdict": "yun", "action": {"type": "stop"}, "proposal_id": 7}}
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
        stop = {"decision": {"verdict": "yun", "action": {"type": "stop"}, "proposal_id": 7}}
        guard = {"holding": False, "published_wall_ns": 9_960_000_000,
                 "lease_received_wall_ns": 9_940_000_000, "nonce": "current",
                 "active_digest": "0" * 64, "goal_sequence": 0}
        return SimpleNamespace(states=[(9.97, state)], outcomes=[(9.95, stop)],
            guard_states=[(9.98, dict(guard))], base_guard_states=[(9.98, dict(guard))],
            joint=SimpleNamespace(name=["j1", "j2", "j3", "j4"], velocity=[0.] * 4),
            joint_received=9.98, odom_received=9.98, speed=lambda: 0.)

    def ready(self, world, sent=9.93):
        return gazebo_rearm_ready(world, sent, 10., ["j1", "j2", "j3", "j4"],
                                  {"arm": "current", "base": "current"}, 7)

    def test_gazebo_reset_needs_current_stop_state_and_both_controller_reports(self):
        world = self.fixture()
        self.assertTrue(self.ready(world))
        self.assertFalse(self.ready(world, sent=9.96))
        changed = self.fixture()
        changed.outcomes[-1][1]["decision"]["proposal_id"] = 6
        self.assertFalse(self.ready(changed))
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

    def test_activation_requires_post_reset_publication_and_rotated_nonce(self):
        world = self.fixture()
        previous = {"arm": "old", "base": "old"}
        self.assertTrue(activated_guards_ready(world, 9.93, 10., previous))
        for field in ("guard_states", "base_guard_states"):
            for delta in ({"nonce": "old"}, {"published_wall_ns": 9_920_000_000}):
                changed = copy.deepcopy(world)
                getattr(changed, field)[-1][1].update(delta)
                self.assertFalse(activated_guards_ready(changed, 9.93, 10., previous))

    def test_actual_preparation_bounds_off_only_requests_and_uses_exact_final_id(self):
        tree = ast.parse((Path(__file__).parents[1] / 'gazebo/run_reference.py').read_text())
        method = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'prepare_arm_fault')
        for misses, counter_delta, requests in ((0, 1, 1), (1, 1, 2), (2, 1, 2), (1, 2, 1)):
            with self.subTest(misses=misses, counter_delta=counter_delta):
                world = self.fixture()
                emitted, sent, observed_ids = [], [], []
                counter = [7]
                def propose(value):
                    sent.append(value)
                    counter[0] += counter_delta
                world.propose_base = propose
                world._emit = lambda *args, **values: emitted.append(values)
                remaining = [misses]
                def wait(predicate, timeout, processes, description):
                    self.assertLessEqual(timeout, 5)
                    if remaining[0]:
                        remaining[0] -= 1
                        raise TimeoutError(description)
                    self.assertTrue(predicate())
                def ready(*args):
                    observed_ids.append(args[-1])
                    return args[-1] == counter[0]
                scope = dict(time=SimpleNamespace(monotonic=lambda: 10.), json=json,
                             ARM_JOINTS=['j1', 'j2', 'j3', 'j4'], wait_for=wait,
                             gazebo_rearm_ready=ready)
                exec(compile(ast.Module(body=[method], type_ignores=[]), 'actual-preparation', 'exec'), scope)
                roles = SimpleNamespace(counter=lambda role: counter[0])
                if misses < 2 and counter_delta == 1:
                    result = scope['prepare_arm_fault'](world, {}, roles)
                    self.assertEqual(result['accepted_proposal_id'], 7 + requests)
                    self.assertEqual(observed_ids, [7 + requests])
                    self.assertEqual(len(emitted), 1)
                else:
                    with self.assertRaises(TimeoutError):
                        scope['prepare_arm_fault'](world, {}, roles)
                    self.assertFalse(emitted)
                self.assertEqual(sent, [0.] * requests)

    def test_nonsecure_preparation_does_not_retry_without_durable_counter(self):
        tree = ast.parse((Path(__file__).parents[1] / 'gazebo/run_reference.py').read_text())
        method = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'prepare_arm_fault')
        world = self.fixture()
        requests = []
        world.propose_base = requests.append
        world._emit = lambda *args, **kwargs: self.fail('failed preparation cannot emit success')
        def wait(predicate, timeout, processes, description):
            self.assertEqual(timeout, 5)
            raise TimeoutError(description)
        scope = dict(time=SimpleNamespace(monotonic=lambda: 10.), json=json,
                     ARM_JOINTS=['j1', 'j2', 'j3', 'j4'], wait_for=wait,
                     gazebo_rearm_ready=lambda *args: False)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'actual-nonsecure-preparation', 'exec'), scope)
        with self.assertRaises(TimeoutError):
            scope['prepare_arm_fault'](world, {}, None)
        self.assertEqual(requests, [0.])
