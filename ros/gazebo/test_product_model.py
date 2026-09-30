"""Product import provenance and the policy/URDF joint contract."""
import gzip
import hashlib
import json
from pathlib import Path
import unittest

from product_model import ARM_JOINTS, HOME, arm_policy

ROOT = Path(__file__).resolve().parents[2]


class ProductModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asset = json.loads((ROOT / "sim/assets/rosbot-xl.json").read_text())

    def test_pinned_sources_and_generated_assets_are_current(self):
        asset = self.asset
        vendor = ROOT / "ros/gazebo/vendor"
        manifest = json.loads((vendor / "manifest.json").read_text())
        self.assertEqual(asset["sources"], manifest)
        for repo in manifest:
            self.assertEqual(len(repo["commit"]), 40)
            for relative, digest in repo["files"].items():
                source = vendor / repo["directory"] / relative
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest, str(source))
        for key, source in (("adapter_sha256", "ros/gazebo/rosbot_xl.urdf.xacro"),
                            ("converter_sha256", "tools/build_product_visuals.py")):
            self.assertEqual(asset[key], hashlib.sha256((ROOT / source).read_bytes()).hexdigest())
        self.assertEqual(gzip.decompress((ROOT / "sim/assets/rosbot-xl.json.gz").read_bytes()),
                         (ROOT / "sim/assets/rosbot-xl.json").read_bytes())

    def test_all_four_arm_limits_match_the_manufacturer_and_policy_is_conservative(self):
        joints = {j["name"]: j for j in self.asset["joints"]}
        policy = arm_policy()
        self.assertLessEqual(policy["max_duration_ms"], 1000)
        for setting, home, name in zip(policy["joints"], HOME, ARM_JOINTS):
            joint = joints[name]
            self.assertAlmostEqual(joint["initial"], home)
            self.assertEqual(setting["min_position"], joint["limit"]["lower"])
            self.assertEqual(setting["max_position"], joint["limit"]["upper"])
            self.assertLessEqual(setting["max_velocity"], joint["limit"]["velocity"])
        self.assertEqual(joints["gripper_right_joint"]["mimic"]["joint"], "gripper_left_joint")
        self.assertEqual(joints["fl_wheel_joint"]["origin"]["xyz"], [.085, .124, 0])
        wheel = next(link for link in self.asset["links"] if link["name"] == "fl_wheel_link")
        values = self.asset["meshes"][wheel["visuals"][0]["mesh"]]["parts"][0]["positions"]
        # Imported wheel geometry has ~48 mm radius, not the old 100 mm wheel.
        self.assertGreater(.048 + min(values[2::3]), 0)
        self.assertLess(.048 + min(values[2::3]), .001)


if __name__ == "__main__":
    unittest.main()
