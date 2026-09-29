"""Exercise the browser stream over a real loopback HTTP connection."""

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from live_stream import LiveHub, start_server


class LiveStreamTest(unittest.TestCase):
    def setUp(self):
        self.hub = LiveHub()
        self.server = start_server(self.hub, 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_static_page_and_stream_live_message(self):
        with urlopen(self.base + "/", timeout=3) as response:
            self.assertIn("Gazebo 실시간 관측", response.read().decode())
        with urlopen(self.base + "/gazebo-live.js", timeout=3) as response:
            self.assertIn("EventSource('/events')", response.read().decode())
        with self.assertRaises(HTTPError) as failure:
            urlopen(self.base + "/../run_reference.py", timeout=3)
        self.assertEqual(failure.exception.code, 404)
        with self.assertRaises(HTTPError) as failure:
            urlopen(Request(self.base + "/events",
                            headers={"Origin": "https://untrusted.example"}), timeout=3)
        self.assertEqual(failure.exception.code, 403)

        received = []

        def read_event():
            request = Request(self.base + "/events", headers={"Origin": self.base})
            with urlopen(request, timeout=5) as response:
                for line in response:
                    if line.startswith(b"data: "):
                        received.append(json.loads(line[6:]))
                        break

        reader = threading.Thread(target=read_event)
        reader.start()
        self.assertTrue(self.hub.viewer_seen.wait(2))
        self.hub.publish({"kind": "telemetry", "sim_ms": 1234, "x": 5.01,
                          "y": 5.0, "speed": 0.1, "joint": 0.01, "humans": []})
        reader.join(timeout=3)
        self.assertFalse(reader.is_alive())
        self.assertEqual(received[0]["kind"], "telemetry")
        self.assertEqual(received[0]["x"], 5.01)


if __name__ == "__main__":
    unittest.main()
