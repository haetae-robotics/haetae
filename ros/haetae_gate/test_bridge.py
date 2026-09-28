import json
import sys
import unittest

from bridge import Bridge, BridgeFailure


class BridgeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
