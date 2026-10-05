import unittest
from types import SimpleNamespace as S
from proposals import base_action, arm_action, InvalidProposal, semantic_binding, semantic_frame, SEMANTIC_PREFIX
import json


def twist(x=0, y=0):
    return S(twist=S(linear=S(x=x,y=y,z=0),angular=S(x=0,y=0,z=0)))


class ProposalTests(unittest.TestCase):
    def test_semantic_references_never_contain_authoritative_facts(self):
        binding = {"schema_version": 1, "world_revision": 1, "task_revision": 1,
                   "task_id": "fixture", "step_id": "motion", "robot_id": "robot",
                   "model_sha256": "a" * 64, "tool_id": "tool", "item_id": "item"}
        self.assertEqual(semantic_binding(semantic_frame(binding)), binding)
        self.assertIsNone(semantic_binding("base_link"))
        for changed in ({**binding, "world_revision": True}, {**binding, "world_revision": 0},
                        {**binding, "schema_version": 2}, {**binding, "item": "inert"},
                        {**binding, "coverage_known": True}, {**binding, "task_id": " "},
                        {**binding, "model_sha256": "A" * 64}):
            with self.assertRaises(InvalidProposal):
                semantic_binding(SEMANTIC_PREFIX + json.dumps(changed))
        for text in ("[]", '{"task_id":"a","task_id":"b"}', "x" * 2049):
            with self.assertRaises(InvalidProposal):
                semantic_binding(SEMANTIC_PREFIX + text)

    def test_base_shapes(self):
        self.assertEqual(base_action(twist(), 200), {"type": "stop"})
        self.assertEqual(base_action(twist(.2), 200)["ttl_ms"], 200)
        for msg in (twist(y=1), twist(float('nan'))):
            with self.assertRaises(InvalidProposal):
                base_action(msg, 200)

    def test_arm_cannot_drop_names_or_supplemental_fields(self):
        point = S(positions=[.1], velocities=[], accelerations=[], effort=[],
                  time_from_start=S(sec=0,nanosec=100000000))
        msg = S(joint_names=['joint1'],points=[point])
        self.assertEqual(arm_action(msg,['joint1'])['ttl_ms'],100)
        self.assertEqual(arm_action(msg,['joint1'],1000)['ttl_ms'],1000)
        for fixed in (True, -1, 2000):
            with self.assertRaises(InvalidProposal): arm_action(msg,['joint1'],fixed)
        point.time_from_start.sec=2
        with self.assertRaises(InvalidProposal): arm_action(msg,['joint1'],1000)
        point.time_from_start.sec=0
        point.velocities=[1]
        with self.assertRaises(InvalidProposal): arm_action(msg,['joint1'])
        point.velocities=[]
        point.positions=[float('inf')]
        with self.assertRaises(InvalidProposal): arm_action(msg,['joint1'])
        with self.assertRaises(InvalidProposal): arm_action(msg,['other'])
