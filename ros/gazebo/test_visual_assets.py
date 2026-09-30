"""Keep the native and browser robot geometry in agreement."""
import json
import math
import runpy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


class VisualAssetTest(unittest.TestCase):
    def test_generated_assets_are_current_and_wheels_clear_lower_body(self):
        author = runpy.run_path(str(ROOT / "tools/build_reference_visuals.py"))
        author["build"]()
        rig = json.loads((ROOT / "sim/assets/haetae-rig.json").read_text())
        self.assertEqual(rig["parts"], author["PARTS"])
        self.assertEqual(rig["materials"], author["PALETTE"])
        wheel_depth = max(abs(z) for p in rig["parts"] if p["link"] == "wheel"
                          for z in p["positions"][2::3])
        for part in rig["parts"]:
            if part["link"] == "chassis" and min(part["positions"][2::3]) < .2:
                self.assertLess(max(abs(y) for y in part["positions"][1::3]),
                                .18 - wheel_depth, part["name"])

    def test_both_renderers_use_identical_bounded_geometry(self):
        rig = json.loads((ROOT / "sim/assets/haetae-rig.json").read_text())
        bounds = {"chassis": ((-.22, -.17, .09), (.22, .17, .36)),
                  "arm": ((-.051, -.051, -.03), (.32, .051, .046)),
                  "wheel": ((-.101, -.101, -.04), (.101, .101, .04))}
        for link, (lower, upper) in bounds.items():
            parts = [p for p in rig["parts"] if p["link"] == link]
            self.assertTrue(parts)
            dae = ET.parse(ROOT / "ros/gazebo/meshes" / (link + ".dae"))
            geometries = dae.findall("c:library_geometries/c:geometry", NS)
            self.assertEqual(len(parts), len(geometries))
            for part, geometry in zip(parts, geometries):
                positions = part["positions"]
                self.assertEqual(len(positions), len(part["normals"]))
                self.assertTrue(all(math.isfinite(v) for v in positions + part["normals"]))
                self.assertTrue(all(0 <= i < len(positions) // 3 for i in part["indices"]))
                for axis in range(3):
                    self.assertGreaterEqual(min(positions[axis::3]), lower[axis], part["name"])
                    self.assertLessEqual(max(positions[axis::3]), upper[axis], part["name"])
                sources = geometry.findall("c:mesh/c:source/c:float_array", NS)
                self.assertEqual([float(v) for v in sources[0].text.split()], positions)
                self.assertEqual([float(v) for v in sources[1].text.split()], part["normals"])
                triangles = geometry.find("c:mesh/c:triangles/c:p", NS)
                self.assertEqual([int(v) for v in triangles.text.split()][::2], part["indices"])


if __name__ == "__main__":
    unittest.main()
