import json
import sys
import unittest

from bridge import (Bridge, BridgeFailure, ExpiredActuation, require_fresh_actuation,
                    lease_renewable, reject_expired_actuation)


class BridgeTests(unittest.TestCase):
    def test_arm_lease_requires_fresh_healthy_engine_response(self):
        status = {"mode": "normal", "recorder_ok": True, "state_ok": True, "world_age_ms": 10}
        self.assertTrue(lease_renewable({"status": status}, 20, 200, 50))
        for change, elapsed in (({}, 50), ({}, -1), ({"world_age_ms": 180}, 20),
                                ({"mode": "hold"}, 1), ({"recorder_ok": False}, 1),
                                ({"state_ok": False}, 1), ({"world_age_ms": None}, 1)):
            self.assertFalse(lease_renewable({"status": {**status, **change}}, elapsed, 200, 50))

    def test_expiry_rejects_without_renewing_authority_or_masking_slow_response(self):
        step = {"cmd": {"linear": 0.5, "angular": 0},
                "status": {"active_expires_ms": 2000, "world_age_ms": 195}}
        with self.assertRaises(ExpiredActuation):
            require_fresh_actuation(step, 6, 1006, 200, 50)
        with self.assertRaises(BridgeFailure) as failure:
            require_fresh_actuation(step, 50, 1050, 200, 50)
        self.assertNotIsInstance(failure.exception, ExpiredActuation)
        safe = {"cmd": {"linear": 0, "angular": 0}, "arm": "cancel", "status": {"armed": []}}
        class Reply:
            def request(inner, request):
                inner.requested = request
                return inner.reply
        bridge = Reply()
        bridge.reply = safe
        self.assertEqual(reject_expired_actuation(bridge, "expired", 1006), safe)
        self.assertEqual(bridge.requested, {"k": "reject", "t": 1006, "reason": "expired"})
        for unsafe in ({**safe, "cmd": {"linear": .1, "angular": 0}},
                       {**safe, "arm": {"execute": {}}},
                       {**safe, "status": {"armed": ["vla"]}}):
            bridge.reply = unsafe
            with self.assertRaises(BridgeFailure):
                reject_expired_actuation(bridge, "expired", 1006)

    def fake(self, code, timeout=100):
        bridge = Bridge([sys.executable, "-u", "-c", code], timeout_ms=timeout)
        self.addCleanup(bridge.close)
        return bridge

    def test_good_reply(self):
        code = "import sys; sys.stdin.readline(); print('{\"cmd\":{\"linear\":0,\"angular\":0}}',flush=True)"
        self.assertEqual(self.fake(code).request({"k": "tick", "t": 1})["cmd"]["linear"], 0)

    def test_timeout_eof_and_malformed_reply(self):
        cases = [
            ("import sys,time; sys.stdin.readline(); time.sleep(2)", 20),
            ("import sys; sys.stdin.readline()", 100),
            ("import sys; sys.stdin.readline(); print('broken',flush=True)", 100),
            ("import sys; sys.stdin.readline(); print('{\"cmd\":{\"linear\":NaN,\"angular\":0}}',flush=True)", 100),
        ]
        for code, timeout in cases:
            with self.subTest(code=code):
                with self.assertRaises(BridgeFailure):
                    self.fake(code, timeout).request({"k": "tick", "t": 1})

    def test_oversized_request_is_refused_before_write(self):
        bridge = self.fake("import time; time.sleep(2)")
        with self.assertRaises(BridgeFailure):
            bridge.request({"data": "a" * (1024 * 1024)})

    def test_late_motion_cannot_reset_controller_deadman(self):
        step = {"cmd": {"linear": 0.5, "angular": 0.0},
                "status": {"active_expires_ms": 1200, "world_age_ms": 20}}
        require_fresh_actuation(step, 40, 1040, 200, 50)
        for elapsed, now, age, expiry in (
            (60, 1060, 20, 1200),   # response budget exceeded
            (40, 1200, 20, 1200),   # proposal expired
            (40, 1040, 170, 1200),  # world basis expired
        ):
            with self.subTest(elapsed=elapsed, now=now, age=age):
                late = {"cmd": step["cmd"], "status": {
                    "active_expires_ms": expiry, "world_age_ms": age}}
                with self.assertRaisesRegex(BridgeFailure, "stale actuation"):
                    require_fresh_actuation(late, elapsed, now, 200, 50)
        arm = {"cmd": {"linear": 0, "angular": 0}, "arm": {"execute": {}},
               "status": {"active_expires_ms": 2000, "world_age_ms": 20}}
        with self.assertRaises(BridgeFailure):
            require_fresh_actuation(arm, 60, 1060, 200, 50)
        for stop in ({"cmd": {"linear": 0, "angular": 0}},
                     {"cmd": {"linear": 0, "angular": 0}, "arm": "cancel"}):
            require_fresh_actuation(stop, 500, 1500, 200, 50)


if __name__ == "__main__":
    unittest.main()
