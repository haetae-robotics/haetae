"""Fail-closed sensor fusion, independent of Gazebo/ROS Python bindings."""

import math
import time
import unittest
from types import SimpleNamespace as N
from lidar_perception import COUNT, Perception, decode_scan
from native_person import geometry_poses


def scan(stamp=1000):
    ranges = [float("inf")] * COUNT
    step = 2 * math.pi / (COUNT - 1)
    index = round((math.pi / 2 + math.pi) / step)
    ranges[index] = 1.93  # Native calibration cylinder near (3,7).
    return N(
        frame="bay_lidar::sensor::bay_scan",
        header=N(stamp=N(sec=stamp // 1000, nsec=stamp % 1000 * 1_000_000)),
        count=COUNT,
        vertical_count=1,
        ranges=ranges,
        angle_min=-math.pi,
        angle_max=math.pi,
        angle_step=step,
        range_min=0.1,
        range_max=10.0,
        vertical_angle_min=0.0,
        vertical_angle_max=0.0,
        world_pose=N(
            position=N(x=3.0, y=5.0, z=1.16), orientation=N(x=0.0, y=0.0, z=0.0, w=1.0)
        ),
    )


class LidarTest(unittest.TestCase):
    def test_empty_bay_requires_live_calibrated_complete_scan(self):
        p = Perception()
        self.assertEqual(p.snapshot(1000, (5, 5))[1], 0)
        p.receive(scan())
        humans, confidence, info = p.snapshot(1050, (5, 5))
        self.assertEqual(humans, [])
        self.assertEqual(confidence, 1)
        self.assertTrue(info["healthy"])
        self.assertEqual(p.snapshot(1200, (5, 5))[1], 0)
        self.assertEqual(p.snapshot(999, (5, 5))[1], 0)
        self.assertEqual(p.snapshot(1050, (2.0, 5))[1], 0)
        p.frame = type(p.frame)(1000, time.monotonic() - 0.201, (), True, "ok")
        self.assertEqual(p.snapshot(1050, (5, 5))[1], 0)

    def test_obstacle_measurement_comes_only_from_ranges(self):
        msg = scan()
        index = round(math.pi / msg.angle_step)
        msg.ranges[index] = 2.5
        p = Perception()
        p.receive(msg)
        humans, confidence, _ = p.snapshot(1050, (5, 5))
        self.assertEqual(confidence, 1)
        self.assertEqual(len(humans), 1)
        self.assertAlmostEqual(humans[0]["pos"]["x"], 5.5, places=3)
        self.assertLess(abs(humans[0]["pos"]["y"] - 5), 0.012)

    def test_corrupt_missing_and_replayed_frames_cannot_refresh_coverage(self):
        msg = scan()
        msg.ranges[20] = float("nan")
        self.assertFalse(decode_scan(msg, time.monotonic()).healthy)
        msg = scan()
        msg.ranges = [float("inf")] * COUNT
        self.assertFalse(decode_scan(msg, time.monotonic()).healthy)
        msg = scan()
        msg.world_pose.position.x = 4
        self.assertFalse(decode_scan(msg, time.monotonic()).healthy)
        msg = scan()
        msg.ranges.pop()
        self.assertFalse(decode_scan(msg, time.monotonic()).healthy)
        p = Perception()
        p.receive(scan())
        first = p.frame.received
        p.receive(scan())
        p.receive(scan(999))
        self.assertEqual(p.frames, 1)
        self.assertEqual(p.frame.received, first)
        msg = scan(1050)
        msg.ranges[0] = -float("inf")
        p.receive(msg)
        self.assertEqual(p.snapshot(1051, (5, 5))[1], 0)

    def test_native_gait_has_planted_feet_and_same_body_root(self):
        a = geometry_poses((5, 6), {"distance_m": 0.01, "heading": 0})
        b = geometry_poses((5.04, 6), {"distance_m": 0.05, "heading": 0})
        self.assertEqual(a["torso"][0], (5, 6, 1.16))
        self.assertAlmostEqual(a["foot-1"][0][0], b["foot-1"][0][0])
        self.assertGreaterEqual(a["foot-1"][0][2], 0.027)
        for _, q in a.values():
            self.assertAlmostEqual(sum(v * v for v in q), 1)


if __name__ == "__main__":
    unittest.main()
