#!/usr/bin/env python3
"""Signed gate to ROS action-server reference test for one joint.

The reference server has a 250 ms independent heartbeat timeout. This is a
software contract test, not evidence that a real arm controller stops.
"""

import json
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import rclpy
from control_msgs.action import FollowJointTrajectory
from geometry_msgs.msg import TwistStamped
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from run_scenario import fixture, public


class ArmWorld(Node):
    def __init__(self):
        super().__init__("arm_world")
        self.human = False
        self.position = 0.0
        self.states = []
        self.outcomes = []
        self.heartbeat = time.monotonic()
        self.world_pub = self.create_publisher(String, "/haetae_gate/world", 1)
        self.arm_pub = self.create_publisher(JointTrajectory, "/vla/arm", 1)
        self.create_subscription(String, "/haetae_gate/state",
                                 lambda m: self.states.append((time.monotonic(), json.loads(m.data))), 10)
        self.create_subscription(String, "/haetae_gate/outcome",
                                 lambda m: self.outcomes.append((time.monotonic(), json.loads(m.data))), 10)
        self.create_subscription(TwistStamped, "/cmd_vel", self._heartbeat, 1)
        self.create_timer(0.05, self._world)

    def _heartbeat(self, _msg):
        self.heartbeat = time.monotonic()

    def _world(self):
        stamp = self.get_clock().now().nanoseconds // 1_000_000
        people = [{"id": "child", "class": "child", "pos": {"x": 7.0, "y": 5.0}}] if self.human else []
        payload = {"stamp_ms": stamp, "robot": {"pose": {"x": 5.0, "y": 5.0},
                   "joints": [{"name": "shoulder", "position": self.position, "velocity": 0.0}]},
                   "humans": people, "confidence": 1.0}
        self.world_pub.publish(String(data=json.dumps(payload)))

    def propose(self, values=(), names=("shoulder",)):
        msg = JointTrajectory()
        msg.joint_names = list(names)
        for millis, value in zip((0, 500, 1000), values):
            point = JointTrajectoryPoint()
            point.positions = [value]
            point.time_from_start.sec, point.time_from_start.nanosec = divmod(millis, 1000)
            point.time_from_start.nanosec *= 1_000_000
            msg.points.append(point)
        self.arm_pub.publish(msg)


class ReferenceArm(Node):
    def __init__(self, world):
        super().__init__("reference_arm")
        self.world = world
        self.accepted = []
        self.cancel_requests = []
        self.stops = []
        self.deadman_heartbeats = []
        self.server = ActionServer(
            self, FollowJointTrajectory,
            "/joint_trajectory_controller/follow_joint_trajectory",
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=self._cancel,
            callback_group=ReentrantCallbackGroup())

    def _goal(self, _request):
        self.accepted.append(time.monotonic())
        return GoalResponse.ACCEPT

    def _cancel(self, _goal_handle):
        self.cancel_requests.append(time.monotonic())
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle):
        started = time.monotonic()
        while time.monotonic() - started < 1.5:
            now = time.monotonic()
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                self.stops.append((now, "cancel"))
                return FollowJointTrajectory.Result()
            if now - self.world.heartbeat >= 0.25:
                self.deadman_heartbeats.append(self.world.heartbeat)
                goal_handle.abort()
                self.stops.append((now, "deadman"))
                return FollowJointTrajectory.Result()
            time.sleep(0.005)
        goal_handle.succeed()
        self.stops.append((time.monotonic(), "complete"))
        return FollowJointTrajectory.Result()


def until(predicate, gate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gate.poll() is not None:
            raise AssertionError("gate exited early with " + str(gate.returncode))
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"arm test timed out at line {inspect.stack()[1].lineno}")


def kill_gate(gate):
    child_file = Path(f"/proc/{gate.pid}/task/{gate.pid}/children")
    children = [int(pid) for pid in child_file.read_text().split()] if child_file.exists() else []
    if gate.poll() is None:
        gate.kill()
        gate.wait(timeout=3)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if all(not Path(f"/proc/{pid}/stat").exists() or
               Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z" for pid in children):
            return
        time.sleep(0.01)
    raise AssertionError("orphaned Rust gate did not exit")


def main(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fixture(root, binary, arm=True)
        rclpy.init()
        world = ArmWorld()
        server = ReferenceArm(world)
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(world)
        executor.add_node(server)
        thread = threading.Thread(target=executor.spin, daemon=True)
        thread.start()
        script = Path(__file__).resolve().parents[1] / "haetae_gate/node.py"
        with (root / "gate.stderr").open("wb") as stderr:
            gate = subprocess.Popen([sys.executable, str(script), "--ros-args", "--params-file",
                                     str(root / "params.yaml")], stderr=stderr)
            try:
                def arm_source():
                    sent = time.monotonic()
                    world.propose()
                    until(lambda: any(t >= sent and "vla" in state["armed"]
                                      for t, state in world.states), gate)

                def settled_after(reset_at):
                    until(lambda: any(t >= reset_at + 0.15 and not state["arm_cancelling"]
                                      for t, state in world.states), gate)

                until(lambda: world.states and world.states[-1][1]["mode"] == "normal", gate)
                arm_source()
                world.propose((0.0, 0.04, 0.04), names=("wrong",))
                until(lambda: any("rejected" in value for _, value in world.outcomes), gate)
                assert not server.accepted
                until(lambda: world.states and not world.states[-1][1]["armed"], gate)
                arm_source()
                world.propose((0.0, 2.0, 2.0))
                until(lambda: any("decision" in value and value["decision"]["verdict"] == "bul"
                                  for _, value in world.outcomes), gate)
                assert not server.accepted
                until(lambda: world.states and not world.states[-1][1]["armed"], gate)
                arm_source()
                world.propose((0.0, 0.04, 0.04))
                until(lambda: len(server.accepted) == 1, gate)
                world.propose((0.0, 0.04, 0.04))
                until(lambda: len(server.stops) == 1 and server.stops[-1][1] == "cancel", gate)
                assert len(server.accepted) == 1, "arm replacement reached the controller"
                settled_after(time.monotonic())
                arm_source()
                world.propose((0.0, 0.04, 0.04))
                until(lambda: len(server.accepted) == 2, gate)
                world.position = 0.2
                until(lambda: len(server.stops) == 2 and server.stops[-1][1] == "cancel", gate)
                world.position = 0.0
                settled_after(time.monotonic())
                arm_source()
                world.propose((0.0, 0.04, 0.04))
                until(lambda: len(server.accepted) == 3, gate)
                trigger = time.monotonic()
                world.human = True
                until(lambda: len(server.stops) == 3 and server.stops[-1][1] == "cancel", gate)
                first_cancel = server.stops[-1][0]
                assert first_cancel - trigger <= 0.3
                world.human = False
                settled_after(time.monotonic())
                arm_source()
                world.propose((0.0, 0.04, 0.04))
                until(lambda: len(server.accepted) == 4, gate)
                killed = time.monotonic()
                kill_gate(gate)
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and not any(
                    t >= killed and reason == "deadman" for t, reason in server.stops
                ):
                    time.sleep(0.01)
                deadman = next((t for t, reason in server.stops if t >= killed and reason == "deadman"), None)
                assert deadman is not None, "reference arm did not stop after gateway death"
                heartbeat = server.deadman_heartbeats[-1]
                assert heartbeat <= deadman - 0.25, "reference arm aborted before heartbeat timeout"
                assert deadman - heartbeat <= 0.35, (
                    f"reference arm heartbeat timeout took {(deadman-heartbeat)*1000:.1f} ms")
                assert deadman - killed <= 0.5, (
                    f"reference arm stop took {(deadman-killed)*1000:.1f} ms after gateway death")
                report = subprocess.run([binary, "sillok", "verify", "--log", str(root / "sillok.jsonl"),
                                         "--pubkey", public(9)], capture_output=True, text=True)
                assert report.returncode == 0 and json.loads(report.stdout)["fully_sealed"]
                print(json.dumps({"ok": True, "malformed_rejected": True,
                                  "bounds_denied": True, "replacement_cancelled": True,
                                  "tracking_cancelled": True,
                                  "human_cancel_ms": round((first_cancel-trigger)*1000, 1),
                                  "kill_deadman_ms": round((deadman-killed)*1000, 1),
                                  "last_heartbeat_deadman_ms": round((deadman-heartbeat)*1000, 1)}))
            finally:
                kill_gate(gate)
                executor.shutdown()
                world.destroy_node()
                server.destroy_node()
                rclpy.shutdown()
                thread.join(timeout=2)
                if (root / "gate.stderr").stat().st_size:
                    print((root / "gate.stderr").read_text(), file=sys.stderr)


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]))
