import unittest

from kinematics import Base
from oracle import disc_gap, disc_hits_rect, stop_latency_ms


class SimulationTests(unittest.TestCase):
    def test_deadman_decelerates_to_zero_without_fresh_commands(self):
        base = Base(speed=0.8)
        base.command(0.8, 0.0, 1.0)
        for index in range(1, 111):
            base.advance(0.01, 1.0 + index * 0.01)
        self.assertEqual(base.speed, 0.0)
        self.assertGreater(base.x, 5.0)

    def test_acceleration_and_turn(self):
        base = Base()
        base.command(1.0, 1.0, 0.0)
        for index in range(1, 11):
            base.advance(0.01, index * 0.01)
        self.assertAlmostEqual(base.speed, 0.2)
        self.assertGreater(base.y, 5.0)

    def test_pure_turn_expires_with_deadman(self):
        base = Base()
        base.command(0, 1, 0)
        base.advance(.1, .1)
        self.assertAlmostEqual(base.yaw, .1)
        base.advance(.1, .3)
        self.assertAlmostEqual(base.yaw, .1)

    def test_independent_geometry_and_latency(self):
        self.assertAlmostEqual(disc_gap(0, 0, 0.25, 1, 0, 0.2), 0.55)
        self.assertTrue(disc_hits_rect(0.8, 0, 0.25, 1, -1, 2, 1))
        self.assertFalse(disc_hits_rect(0.7, 0, 0.25, 1, -1, 2, 1))
        trace = [{"time_s": 1.0, "speed": 0.2}, {"time_s": 1.08, "speed": 0.0}]
        self.assertEqual(stop_latency_ms(trace, 1.02), 60.0)


if __name__ == "__main__":
    unittest.main()
