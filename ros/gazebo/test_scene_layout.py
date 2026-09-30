"""Regression: nearby reports must not put the silhouette on the robot."""

import json
import math
from pathlib import Path
import unittest

from scene_layout import PERSON_BODY_RADIUS_M, nearby_person, person_entry, PersonWalk
from product_model import PERSON_DISTANCE_M


class SceneLayoutTest(unittest.TestCase):
    def test_walk_follows_sim_time_and_moves_from_far_to_near_without_jumps(self):
        for yaw in (0, math.pi / 2, -math.pi / 3):
            start, end = person_entry(5, 5, yaw), nearby_person(5, 5, yaw)
            walk = PersonWalk(start, end, 1000)
            self.assertGreater(math.dist(start, (5, 5)), 1.9)
            last = start
            distances = []
            for stamp in range(1000, 8001, 50):
                point, motion = walk.sample(stamp)
                self.assertLessEqual(math.dist(last, point), .012 + 1e-9)
                distances.append(math.dist(point, (5, 5)))
                last = point
            self.assertEqual(last, end)
            self.assertTrue(all(a >= b for a, b in zip(distances, distances[1:])))
            self.assertFalse(motion["moving"])
            self.assertEqual(motion["speed_mps"], 0)
            self.assertEqual(walk.sample(500)[0], start)
            self.assertEqual(walk.sample(2000), walk.sample(2000))
            exit_walk = PersonWalk(end, start, 8000, .35, motion["distance_m"])
            self.assertEqual(exit_walk.sample(8000)[0], end)
            self.assertEqual(exit_walk.sample(20000)[0], start)

    def test_person_is_near_but_clear_of_robot_and_arm_sweep(self):
        rig = json.loads((Path(__file__).resolve().parents[2] /
                          "sim/assets/rosbot-xl.json").read_text())
        parents = {j["child"]: j for j in rig["joints"]}
        radii = []
        for link in rig["links"]:
            if link["name"] not in ("link3", "link4", "link5", "gripper_left_link", "gripper_right_link"):
                continue
            child, reach = link["name"], 0.11  # Official rear mounting offset.
            while child != "link3":
                joint = parents[child]
                reach += math.sqrt(sum(v * v for v in joint["origin"]["xyz"]))
                if joint["type"] == "prismatic":
                    reach += max(abs(joint["limit"]["lower"]), abs(joint["limit"]["upper"]))
                child = joint["parent"]
            for visual in link["visuals"]:
                # Triangle inequality covers continuous joint configurations,
                # not just sampled poses. J1/J2 origins add only vertical
                # offsets, so their horizontal contribution is zero.
                radius = max(math.sqrt(sum((p * s + t) ** 2 for p, s, t in
                                 zip(part["positions"][i:i + 3], visual["scale"], visual["origin"]["xyz"])))
                             for part in rig["meshes"][visual["mesh"]]["parts"]
                             for i in range(0, len(part["positions"]), 3))
                radii.append(reach + radius)
        arm_radius = max(radii)
        self.assertLess(arm_radius, 0.54)
        for yaw in (0, math.pi / 2, math.pi, -math.pi / 2):
            x, y = nearby_person(5, 5, yaw)
            self.assertLess(math.hypot(x - 5, y - 5), PERSON_DISTANCE_M + 0.25)
            # Transform back into the robot frame; include 8 cm braking travel.
            px = (x - 5) * math.cos(yaw) + (y - 5) * math.sin(yaw)
            py = -(x - 5) * math.sin(yaw) + (y - 5) * math.cos(yaw)
            for travel in (0, 0.08):
                self.assertGreater(math.hypot(px - travel, py) - arm_radius,
                                   PERSON_BODY_RADIUS_M + 0.005)
                self.assertGreater(math.hypot(px - travel, py) - 0.25, PERSON_BODY_RADIUS_M)


if __name__ == "__main__":
    unittest.main()
