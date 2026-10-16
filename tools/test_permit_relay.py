"""The real relay loop with deterministic controller lease and scheduler fakes."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import permit_link


def run_relay(delayed_state):
    clock, requested, executed, pauses, stopped = [0.], [], [], [], []
    state = {"phase": "OFF", "renewed": None}

    def expired():
        return state["renewed"] is not None and clock[0] - state["renewed"] >= .200

    class Link:
        current = {"sequence": 0}
        def __init__(self, *args):
            pass
        def query(self, op):
            if op == "STATUS":
                status = "LOCKED" if expired() else state["phase"]
                clock[0] += .003  # The STATUS reply may precede the lease boundary.
                return status
            if op == "STOP":
                state["phase"] = "OFF"
            return "LOCKED"
        def execute(self, request, permit, timeout):
            executed.append(request["op"])
            if request["op"] in ("ARM", "RUN"):
                state.update(phase="ARMED" if request["op"] == "ARM" else "ON", renewed=clock[0])
        def stop(self):
            stopped.append(True)
            state["phase"] = "OFF"
        def close(self):
            pass

    class Peer:
        def connect(self, path):
            pass
        def settimeout(self, timeout):
            pass
        def sendall(self, raw):
            requested.append(json.loads(raw)["op"])
        def recv(self, size):
            if requested[-1] == "RUN" and (expired() or requested.count("RUN") == 3):
                return b'{"kind":"terminal","reason":"gate_denied"}\n'
            return b'{"kind":"permit","remaining_ms":50}\n'
        def close(self):
            pass

    def sleep(seconds):
        pauses.append((state["phase"], seconds))
        if seconds == .025 and state["phase"] == delayed_state:
            clock[0] += .198 if delayed_state == "ARMED" else .210
        else:
            clock[0] += seconds

    with patch.object(permit_link, "PermitLink", Link), \
            patch.object(permit_link.socket, "socket", lambda *args: Peer()), \
            patch.object(permit_link.time, "sleep", sleep), \
            patch.object(permit_link.time, "monotonic", lambda: clock[0]), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        try:
            permit_link.relay("fake-port", "public-install", "fake-socket")
            error = None
        except permit_link.LinkError as exc:
            error = str(exc)
    return requested, executed, pauses, stopped, error, output.getvalue()


class RelayBootstrapTest(unittest.TestCase):
    def test_first_run_does_not_yield_away_the_off_only_arm_lease(self):
        requested, executed, pauses, stopped, error, output = run_relay("ARMED")
        self.assertIsNone(error)
        self.assertEqual(executed, ["BIND", "ARM", "RUN", "RUN"])
        self.assertEqual(requested, ["BIND", "ARM", "RUN", "RUN", "RUN"])
        self.assertIn("허용: 보드가 서명을 확인함, LED ON", output)
        self.assertEqual(pauses, [("OFF", .5), ("ON", .025), ("ON", .025)])
        self.assertEqual(stopped, [True])

    def test_a_lease_loss_after_positive_output_still_latches_without_rearm(self):
        requested, executed, _, stopped, error, output = run_relay("ON")
        self.assertIn("허용: 보드가 서명을 확인함, LED ON", output)
        self.assertEqual(executed, ["BIND", "ARM", "RUN"])
        self.assertEqual(requested, executed)
        self.assertEqual(error, "controller locked; explicit operator restart required")
        self.assertEqual(stopped, [True])


if __name__ == "__main__":
    unittest.main()
