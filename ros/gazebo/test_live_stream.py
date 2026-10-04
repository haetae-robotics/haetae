"""Exercise the browser stream over a real loopback HTTP connection."""

import json
import gzip
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
            page = response.read().decode()
            self.assertIn("위험한 명령이 오면", page)
            self.assertIn("시뮬레이션 시작", page)
        with urlopen(self.base + "/gazebo-live.js", timeout=3) as response:
            self.assertIn("EventSource('/events')", response.read().decode())
        with urlopen(self.base + "/gazebo-scene.js", timeout=3) as response:
            self.assertIn("WebGLRenderer", response.read().decode())
        with urlopen(self.base + "/gazebo-live.css", timeout=3) as response:
            self.assertEqual(response.headers.get_content_type(), "text/css")
        with urlopen(self.base + "/assets/haetae-rig.json", timeout=3) as response:
            self.assertEqual(json.load(response)["coordinates"], "ROS z-up")
        with urlopen(self.base + "/assets/rosbot-xl.json", timeout=3) as response:
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            model = json.loads(gzip.decompress(response.read()))
            self.assertEqual(model["model"], "ROSbot XL + OpenMANIPULATOR-X")
            self.assertEqual(model["root"], "base_link")
        with urlopen(self.base + "/vendor/three/OrbitControls.js", timeout=3) as response:
            self.assertIn("OrbitControls", response.read().decode())
        with urlopen(self.base + "/viewer-config", timeout=3) as response:
            self.assertEqual(json.load(response), {"gazebo_gui": False, "gazebo_gui_port": 6080,
                                                  "manual_start": False,
                                                  "attack_probes": False,
                                                  "secured_gazebo": False,
                                                  "step_through": False})
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

    def test_untrusted_host_is_rejected_even_without_origin(self):
        for endpoint in ("/health", "/viewer-config", "/events"):
            with self.subTest(endpoint=endpoint), self.assertRaises(HTTPError) as failure:
                urlopen(Request(self.base + endpoint, headers={"Host": "attacker.invalid"}), timeout=3)
            self.assertEqual(failure.exception.code, 403)

    def test_gui_mode_is_advertised_only_when_enabled(self):
        server = start_server(self.hub, 0, gazebo_gui=True, manual_start=True,
                              attack_probes=True, secured_gazebo=True, step_through=True)
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urlopen(base + "/viewer-config", timeout=3) as response:
                self.assertEqual(json.load(response), {"gazebo_gui": True, "gazebo_gui_port": 6080,
                                                      "manual_start": True,
                                                      "attack_probes": True,
                                                      "secured_gazebo": True,
                                                      "step_through": True})
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(base + "/start", data=b"",
                                headers={"Origin": "https://untrusted.example"}), timeout=3)
            self.assertEqual(failure.exception.code, 403)
            self.assertFalse(self.hub.start_requested.is_set())
            with urlopen(Request(base + "/start", data=b"",
                                 headers={"Origin": base}), timeout=3) as response:
                self.assertTrue(json.load(response)["started"])
            self.assertTrue(self.hub.start_requested.is_set())
        finally:
            server.shutdown()
            server.server_close()

    def test_late_viewer_receives_start_phase_after_telemetry_rollover(self):
        self.hub.publish({"kind": "phase", "label": "3D 화면 준비 · 시작 버튼을 누르세요",
                          "sim_ms": 1000})
        for index in range(600):
            self.hub.publish({"kind": "telemetry", "sim_ms": index + 1001})

        request = Request(self.base + "/events",
                          headers={"Last-Event-ID": "previous-run:999999"})
        with urlopen(request, timeout=3) as response:
            for line in response:
                if line.startswith(b"data: "):
                    first = json.loads(line[6:])
                    break
        self.assertEqual(first["kind"], "phase")
        self.assertEqual(first["label"], "3D 화면 준비 · 시작 버튼을 누르세요")

    def test_failure_is_retained_and_disables_start_and_advance(self):
        server = start_server(self.hub, 0, manual_start=True, step_through=True)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            token = self.hub.begin_checkpoint(label="정지", detail="확인", next_label="팔", sim_ms=1000)
            self.hub.fail({"kind": "error", "label": "시뮬레이션 중단"})
            for index in range(600):
                self.hub.publish({"kind": "telemetry", "sim_ms": index})
            self.assertFalse(self.hub.checkpoint_active(token))
            self.assertEqual(sum(row["kind"] == "error" for row in self.hub.after(0, 0)), 1)
            self.assertFalse(any(row["kind"] == "checkpoint" for row in self.hub.after(0, 0)))
            with urlopen(base + "/health", timeout=3) as response:
                self.assertFalse(json.load(response)["ok"])
            for endpoint in ("/start", "/advance"):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(base + endpoint, data=b"", headers={"X-Checkpoint-Token": token}), timeout=3)
                self.assertEqual(failure.exception.code, 409)
        finally:
            server.shutdown()
            server.server_close()

    def test_next_step_requires_current_token_and_survives_late_viewer(self):
        server = start_server(self.hub, 0, step_through=True)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            token = self.hub.begin_checkpoint(label="바퀴 정지 장면", detail="사람 표시 유지",
                                              next_label="팔 시험", sim_ms=1000)
            for index in range(600):
                self.hub.publish({"kind": "telemetry", "sim_ms": index + 1001})
            # A page reload still learns which scene is waiting, even after
            # the regular telemetry buffer has rolled over.
            with urlopen(base + "/events", timeout=3) as response:
                row = next(json.loads(line[6:]) for line in response if line.startswith(b"data: "))
                self.assertEqual(row["token"], token)
                self.assertTrue(row["waiting"])
            for headers, expected in (({}, 409), ({"X-Checkpoint-Token": "old"}, 409),
                                      ({"X-Checkpoint-Token": token,
                                        "Origin": "https://untrusted.example"}, 403)):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(base + "/advance", data=b"", headers=headers), timeout=3)
                self.assertEqual(failure.exception.code, expected)
                self.assertTrue(self.hub.checkpoint_active(token))
            request = Request(base + "/advance", data=b"",
                              headers={"X-Checkpoint-Token": token, "Origin": base})
            with urlopen(request, timeout=3) as response:
                self.assertTrue(json.load(response)["advanced"])
            self.assertFalse(self.hub.checkpoint_active(token))
            next_token = self.hub.begin_checkpoint(label="팔 정지 장면", detail="정지",
                                                   next_label="공격 시험", sim_ms=2000)
            with self.assertRaises(HTTPError) as failure:
                urlopen(request, timeout=3)
            self.assertEqual(failure.exception.code, 409)
            self.assertTrue(self.hub.checkpoint_active(next_token))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
