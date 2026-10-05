"""Guard the attacker's narrow DDS permissions in the Gazebo reference."""

from pathlib import Path
import unittest
from xml.etree import ElementTree


POLICY = Path(__file__).with_name("gazebo.policy.xml")


class GazeboSecurityPolicyTest(unittest.TestCase):
    def test_vla_cannot_publish_to_controller_or_world(self):
        root = ElementTree.parse(POLICY).getroot()
        enclaves = {row.attrib["path"]: row for row in root.findall("./enclaves/enclave")}
        self.assertEqual(set(enclaves), {"/haetae/sim", "/haetae/world",
                                          "/haetae/gate", "/haetae/vla", "/haetae/vla_signer", "/haetae/relay"})
        vla = enclaves["/haetae/vla"]
        self.assertEqual([(row.attrib["ns"], row.attrib["node"])
                          for row in vla.findall("./profiles/profile")],
                         [("/", "*")])
        published = {topic.text for topic in vla.findall(
            "./profiles/profile/topics[@publish='ALLOW']/topic")}
        self.assertEqual(published, {"/vla/cmd_vel", "/vla/arm", "/rosout",
                                     "/ros_discovery_info", "/parameter_events"})
        # DDS certificates define the principal boundary. A ROS node name is
        # caller-selected and cannot isolate two nodes sharing one certificate.
        signer = enclaves["/haetae/vla_signer"]
        signed = {topic.text for topic in signer.findall(
            "./profiles/profile/topics[@publish='ALLOW']/topic")}
        self.assertEqual(signed, {"/haetae_gate/signed/vla", "/rosout",
                                 "/ros_discovery_info", "/parameter_events"})
        self.assertFalse(signer.findall("./profiles/profile/actions[@call='ALLOW']"))
        self.assertFalse(vla.findall("./profiles/profile/actions[@call='ALLOW']"))


if __name__ == "__main__":
    unittest.main()
