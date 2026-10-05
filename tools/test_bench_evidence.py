import json
import unittest

from bench_evidence import scheduling_loss


class BenchEvidenceTest(unittest.TestCase):
    def test_only_proven_availability_loss_is_accepted_as_fail_closed(self):
        marker = {"event": "device_lease_expired", "status": "LOCKED",
                  "host_gap_ms": 250, "automatic_rearm": False}
        events = [{"device_ms": 10, "on": True, "renewed_ms": 10},
                  {"device_ms": 249, "on": False, "reason": "lease"}]
        def log(value):
            return json.dumps(value) + "\ndevice lease locked; explicit new run required"
        self.assertEqual(scheduling_loss(log(marker), 1, "allow", events), events[-1])
        for change in ({"host_gap_ms": 199}, {"host_gap_ms": float("nan")},
                       {"status": "ON"}, {"automatic_rearm": True}):
            changed = dict(marker, **change)
            self.assertIsNone(scheduling_loss(log(changed), 1, "allow", events))
        for code, scenario in ((0, "allow"), (1, "person"), (-9, "allow")):
            self.assertIsNone(scheduling_loss(log(marker), code, scenario, events))
        for changed in (events[1:], events + [{"on": True}],
                        [events[0], dict(events[1], reason="stop")],
                        [events[0], dict(events[1], device_ms=311)]):
            self.assertIsNone(scheduling_loss(log(marker), 1, "allow", changed))
        self.assertIsNone(scheduling_loss("unknown failure", 1, "allow", events))
