import copy
import unittest

from arm_barrier import rearm_ready


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
