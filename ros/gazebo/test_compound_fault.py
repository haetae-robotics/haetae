"""Execute the real compound fault iteration against a scripted gate, without ROS."""
from collections import deque
from functools import partial
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import compound_fault
from stop_evidence import sensor_stop_report

CUT = "trusted world has insufficient controller admission budget"
UNARMED_ERROR = "source must send a zero command to arm"


class ScriptedGate:
    """A proposal burst through a gate that stops on the world-admission cut after the fault."""

    def __init__(self, motion_after_cut=0):
        self.now = 100.0
        self.armed = False
        self.counter = 0
        self.burst = None
        self.cut = False
        self.motion_after_cut = motion_after_cut
        self.world = SimpleNamespace(
            sensor_info={"healthy": True}, states=[], outcomes=[], commands=[], zero_commands=deque(maxlen=512),
            perception=SimpleNamespace(lock=threading.Lock(), drop_frames=False), speed_value=0.,
            marker=lambda *args, **kwargs: None, _emit=lambda *args, **kwargs: None,
            controllers_unlocked=lambda: True, propose_base=self.propose,
            proposal_pipe=SimpleNamespace(write=self.start_burst, flush=lambda: None),
            get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=int(self.now * 1e9))))
        self.world.speed = lambda: self.world.speed_value

    def command(self, linear):
        self.world.commands.append((self.now, linear))
        self.world.speed_value = .1 if linear else 0.
        if linear == 0:
            self.world.zero_commands.append((self.now, int(self.now * 1000)))

    def propose(self, linear):
        self.counter += 1
        world = self.world
        if linear == 0:
            self.armed = True
            world.states.append((self.now, {"stop": "no_command", "armed": ["vla"], "active": None}))
            world.outcomes.extend([(self.now, {"decision": {"verdict": "yun", "action": {"type": "stop"}}})] * 2)
            self.command(0.)
        elif world.perception.drop_frames and not self.cut:
            # The admission cut: the gate's zero, then its denied state and rejection.
            self.cut, self.armed = True, False
            if not self.motion_after_cut:
                self.command(0.)
            world.states.append((self.now, {"stop": "denied", "armed": [], "active": None}))
            world.outcomes.append((self.now, {"rejected": {"error": CUT}}))
        elif self.armed:
            world.states.append((self.now, {"stop": None, "armed": ["vla"], "active": {"id": self.counter}}))
            world.outcomes.append((self.now, {"decision": {"verdict": "yun", "action": {"type": "velocity"}}}))
            self.command(.12)
        else:
            world.outcomes.append((self.now, {"rejected": {"error": UNARMED_ERROR}}))
            world.states.append((self.now, {"stop": "unarmed", "armed": [], "active": None}))
            if self.motion_after_cut:  # A defective gate keeps sending motion after its stop.
                self.motion_after_cut -= 1
                self.command(.12)
            else:
                self.command(0.)

    def start_burst(self, line):
        burst = json.loads(line)["burst"]
        self.burst = [self.now, self.now + burst["seconds"], 1 / burst["hz"]]

    def advance(self, seconds):
        end = self.now + seconds
        while self.now < end:
            self.now = round(self.now + .005, 6)
            self.world.sensor_info["healthy"] = not self.world.perception.drop_frames
            if self.burst and self.burst[0] <= self.now < self.burst[1]:
                self.burst[0] += self.burst[2]
                self.propose(.12)

    def wait_for(self, predicate, timeout, processes, description, action=None):
        deadline, last = self.now + timeout, None
        while self.now < deadline:
            if action and (last is None or self.now - last >= .08):
                action()
                last = self.now
            if predicate():
                return self.now
            self.advance(.01)
        raise TimeoutError(description)

    def run(self):
        stopped = SimpleNamespace(read_text=lambda: "Name:\tsource_node\nState:\tT (stopped)\n")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(compound_fault, "time", SimpleNamespace(monotonic=lambda: self.now, sleep=self.advance)), \
                mock.patch.object(compound_fault, "os", SimpleNamespace(kill=lambda pid, sig: None)), \
                mock.patch.object(compound_fault, "Path", lambda path: stopped):
            return compound_fault.exercise_compound(
                self.world, SimpleNamespace(counter=lambda role: self.counter),
                {"source_world": SimpleNamespace(pid=4321)}, self.wait_for, 1,
                Path(directory) / "compound-faults.json",
                # Only the admission branch runs: no revoked state, so the log is never read.
                partial(sensor_stop_report, Path(directory) / "sillok.jsonl"))


class CompoundStopTest(unittest.TestCase):
    def test_named_stop_without_later_motion_passes(self):
        evidence = ScriptedGate().run()
        self.assertEqual((evidence["ok"], evidence["status"]), (True, "complete"))
        self.assertEqual(evidence["iterations"][0]["stop_reason"], "world_admission_budget_exhausted")
        self.assertGreaterEqual(evidence["iterations"][0]["engine_rejections_after_zero"], 10)

    def test_motion_after_the_named_stop_fails_even_before_a_late_zero(self):
        # No zero at the cut and 300 ms of motion commands after it, then zeros: the
        # first zero after the rejection is late, so only the order rule sees the motion.
        gate = ScriptedGate(motion_after_cut=12)
        with self.assertRaisesRegex(AssertionError, "compound motion command after the stop"):
            gate.run()
        self.assertGreaterEqual(sum(1 for _, value in gate.world.commands if value == 0), 10)


if __name__ == "__main__":
    unittest.main()
