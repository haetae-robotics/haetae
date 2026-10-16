import contextlib
import io
import unittest

from ci_timing import timing_measurement


class TimingMeasurementTest(unittest.TestCase):
    def test_boundary_and_overrun_are_measurements_with_unchanged_limit(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            boundary = timing_measurement("gate", 250, 250)
            self.assertEqual(log.getvalue(), "")
            overrun = timing_measurement("gate", 251, 250)
        self.assertTrue(boundary["within_limit"])
        self.assertEqual(overrun, {"elapsed_ms": 251, "limit_ms": 250, "within_limit": False, "blocking": False})
        self.assertIn("::warning::gate: 251.0 ms exceeds 250 ms", log.getvalue())

    def test_invalid_clock_values_are_errors_rather_than_measurements(self):
        for value in (-1, float("nan"), float("inf"), True, None):
            for elapsed, limit in ((value, 250), (100, value)):
                with self.assertRaises(ValueError):
                    timing_measurement("gate", elapsed, limit)
