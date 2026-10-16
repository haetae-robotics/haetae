"""Stop witnesses follow native audit order, including equal millisecond stamps."""
import unittest

import bench_verify
import permit_verify
import test_bench_attempts as attempts
from bench_evidence import first_stop_after_on


# Reduced from PR34 CI38912155165, Ubuntu person case. ARM and first RUN
# shared device_ms632; choosing OFF by >=632 incorrectly selected ARM.
EVENTS = [
    {"device_ms": 632, "on": False, "reason": "armed", "sequence": 2, "renewed_ms": 631},
    {"device_ms": 632, "on": True, "reason": "run", "sequence": 3, "renewed_ms": 632},
    {"device_ms": 1627, "on": True, "reason": "run", "sequence": 41, "renewed_ms": 1626},
    {"device_ms": 1653, "on": False, "reason": "stop", "sequence": 41, "renewed_ms": 1626}]


class StopOrderTest(unittest.TestCase):
    def test_both_devices_skip_bootstrap_off_at_the_positive_timestamp(self):
        for cls in (bench_verify.Device, permit_verify.Device):
            device = cls.__new__(cls)  # No PTY, process, board or port is opened.
            device.events = lambda: list(EVENTS)
            self.assertEqual(device.stopped(), EVENTS[-1])
            self.assertEqual(device.stopped("stop"), EVENTS[-1])
            self.assertIsNone(device.stopped("armed"))

    def test_a_real_off_immediately_after_on_in_the_same_millisecond_counts(self):
        stopped = dict(EVENTS[-1], device_ms=632, sequence=3, renewed_ms=632)
        self.assertEqual(first_stop_after_on(EVENTS[:2] + [stopped]), stopped)
        self.assertEqual(stopped["device_ms"] - EVENTS[1]["renewed_ms"], 0)

    def test_no_positive_or_no_stop_is_not_evidence(self):
        for events in ([], EVENTS[:1], EVENTS[:3]):
            self.assertIsNone(first_stop_after_on(events))

    def test_an_earlier_wrong_stop_cannot_be_skipped_for_a_later_matching_reason(self):
        lease = dict(EVENTS[-1], device_ms=833, reason="lease", sequence=3, renewed_ms=632)
        later = dict(EVENTS[-1], device_ms=834, reason="signature", sequence=3, renewed_ms=632)
        events = EVENTS[:2] + [lease, later]
        self.assertEqual(first_stop_after_on(events), lease)
        self.assertIsNone(first_stop_after_on(events, "signature"))

    def test_on_after_the_first_off_still_fails_even_if_harness_observed_a_later_on(self):
        stop = dict(EVENTS[-1], device_ms=633, sequence=3, renewed_ms=632)
        resurrected = dict(EVENTS[1], device_ms=634, sequence=4, renewed_ms=634)
        later = dict(stop, device_ms=635, sequence=4, renewed_ms=634)
        events = EVENTS[:2] + [stop, resurrected, later]
        result, _, tried = attempts.PermitHarnessTest().run_attempt(
            events, "허용: 보드가 서명을 확인함, LED ON\n차단: 서명 허가 중단, LED OFF\n", 0,
            late_auth="Authorizer terminal: operator_complete\n", scenario="allow", via_case=True)
        self.assertIsInstance(result, AssertionError)
        self.assertEqual(tried, ["case"])
        result, _, tried = attempts.BenchHarnessTest().run_attempt(
            events, attempts.real_host_log("allow", clean_return=True), attempts.H1_ALLOW_READY,
            0, scenario="allow", via_case=True)
        self.assertIsInstance(result, AssertionError)
        self.assertEqual(tried, ["case"])


if __name__ == "__main__":
    unittest.main()
