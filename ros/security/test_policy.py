"""Prevent accidental protected-writer grants in the reference SROS2 policy."""

from pathlib import Path
import unittest
from xml.etree import ElementTree


POLICY = Path(__file__).with_name("haetae.policy.xml")


class PolicyTests(unittest.TestCase):
    def test_protected_topics_and_arm_action_have_one_writer(self):
        root = ElementTree.parse(POLICY).getroot()
        writers = {}
        callers = {}
        for enclave in root.findall("./enclaves/enclave"):
            path = enclave.attrib["path"]
            for group in enclave.findall("./profiles/profile/topics[@publish='ALLOW']"):
                for topic in group.findall("topic"):
                    writers.setdefault(topic.text, set()).add(path)
            for group in enclave.findall("./profiles/profile/actions[@call='ALLOW']"):
                for action in group.findall("action"):
                    callers.setdefault(action.text, set()).add(path)
        self.assertEqual(writers["/cmd_vel"], {"/haetae/gate"})
        self.assertEqual(writers["/haetae_gate/world"], {"/haetae/world"})
        self.assertEqual(writers["/haetae_gate/fault"], {"/haetae/world"})
        self.assertEqual(writers["/vla/cmd_vel"], {"/haetae/vla"})
        self.assertEqual(writers["/vla/arm"], {"/haetae/vla"})
        self.assertEqual(callers["/joint_trajectory_controller/follow_joint_trajectory"],
                         {"/haetae/gate"})


if __name__ == "__main__":
    unittest.main()
