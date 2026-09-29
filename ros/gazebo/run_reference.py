#!/usr/bin/env python3
"""Exercise Haetae against Gazebo Harmonic and real ros2_control controllers.

This is a physics-backed reference test, not a robot safety certification.
The world publisher uses simulator ground truth, and Gazebo / ros2_control are
trusted. The one-joint arm controller has no independent gate-loss watchdog.
"""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import rclpy
from geometry_msgs.msg import TwistStamped
from nav_msgs.msg import Odometry
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "haetae_sim"))
from run_scenario import fixture, public  # noqa: E402
from live_stream import LiveHub, start_server  # noqa: E402


REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE_CONTROLLER_TOPIC = "/diff_drive_base_controller/cmd_vel"


class GazeboWorld(Node):
    def __init__(self, live=None):
        super().__init__("haetae_gazebo_world", parameter_overrides=[
            Parameter("use_sim_time", Parameter.Type.BOOL, True)],
            automatically_declare_parameters_from_overrides=True)
        self.live = live
        self.odom = None
        self.joint = None
        self.odom_received = 0.0
        self.joint_received = 0.0
        self.human = None
        self.states = []
        self.outcomes = []
        self.commands = []
        self.world_count = 0
        self.world_pub = self.create_publisher(String, "/haetae_gate/world", 1)
        self.vla_pub = self.create_publisher(TwistStamped, "/vla/cmd_vel", 1)
        self.arm_pub = self.create_publisher(JointTrajectory, "/vla/arm", 1)
        self.create_subscription(Odometry, "/diff_drive_base_controller/odom", self._odom, 10)
        self.create_subscription(JointState, "/joint_states", self._joint, 10)
        self.create_subscription(TwistStamped, BASE_CONTROLLER_TOPIC, self._command, 10)
        self.create_subscription(String, "/haetae_gate/state", self._state, 10)
        self.create_subscription(String, "/haetae_gate/outcome", self._outcome, 10)
        self.create_timer(0.05, self._publish_world)

    def _emit(self, kind, **values):
        if self.live:
            self.live.publish({"kind": kind,
                               "sim_ms": self.get_clock().now().nanoseconds // 1_000_000,
                               **values})

    def marker(self, label):
        self._emit("phase", label=label)

    def _state(self, msg):
        value = json.loads(msg.data)
        self.states.append((time.monotonic(), value))
        self._emit("state", mode=value.get("mode"), stop=value.get("stop"),
                   arm_cancelling=value.get("arm_cancelling", False))

    def _outcome(self, msg):
        value = json.loads(msg.data)
        self.outcomes.append((time.monotonic(), value))
        decision = value.get("decision", {})
        if decision:
            self._emit("decision", verdict=decision.get("verdict"),
                       fired=decision.get("fired", []),
                       action=decision.get("action", {}).get("type")
                       if decision.get("action") else None)

    def _odom(self, msg):
        self.odom = msg
        self.odom_received = time.monotonic()

    def _joint(self, msg):
        if "shoulder" in msg.name:
            self.joint = msg
            self.joint_received = time.monotonic()

    def _command(self, msg):
        self.commands.append((time.monotonic(), msg.twist.linear.x))
        self._emit("base_command", linear=msg.twist.linear.x)

    def pose(self):
        if self.odom is None:
            return None
        p = self.odom.pose.pose.position
        # The odom frame starts at the model's spawn pose (5, 5).
        return (5.0 + p.x, 5.0 + p.y)

    def shoulder(self):
        if self.joint is None:
            return None
        return self.joint.position[list(self.joint.name).index("shoulder")]

    def speed(self):
        return 0.0 if self.odom is None else self.odom.twist.twist.linear.x

    def _publish_world(self):
        now = time.monotonic()
        stamp = self.get_clock().now().nanoseconds // 1_000_000
        if (not stamp or self.odom is None or self.joint is None or
                now - self.odom_received > 0.2 or now - self.joint_received > 0.2):
            return
        q = self.odom.pose.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y),
                         1 - 2 * (q.y * q.y + q.z * q.z))
        index = list(self.joint.name).index("shoulder")
        velocity = self.joint.velocity[index] if len(self.joint.velocity) > index else 0.0
        x, y = self.pose()
        payload = {"stamp_ms": stamp,
                   "robot": {"pose": {"x": x, "y": y}, "yaw": yaw,
                             "twist": {"linear": self.speed(), "angular": self.odom.twist.twist.angular.z},
                             "joints": [{"name": "shoulder", "position": self.shoulder(),
                                         "velocity": velocity}]},
                   "humans": ([] if self.human is None else
                              [{"id": "sim-person", "class": "adult",
                                "pos": {"x": self.human[0], "y": self.human[1]}}]),
                   "confidence": 1.0}
        self.world_pub.publish(String(data=json.dumps(payload)))
        self.world_count += 1
        self._emit("telemetry", x=x, y=y, speed=self.speed(),
                   joint=self.shoulder(), humans=payload["humans"])

    def propose_base(self, linear):
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.twist.linear.x = linear
        self.vla_pub.publish(msg)

    def propose_arm(self, target):
        start = self.shoulder()
        assert start is not None
        msg = JointTrajectory()
        msg.joint_names = ["shoulder"]
        for millis, value in ((0, start), (500, target), (1000, target)):
            p = JointTrajectoryPoint()
            p.positions = [value]
            p.time_from_start.sec, p.time_from_start.nanosec = divmod(millis, 1000)
            p.time_from_start.nanosec *= 1_000_000
            msg.points.append(p)
        self.arm_pub.publish(msg)


def wait_for(predicate, timeout, processes, description, action=None):
    deadline = time.monotonic() + timeout
    last_action = 0.0
    while time.monotonic() < deadline:
        for name, process in processes.items():
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited with {process.returncode} while waiting for {description}")
        if predicate():
            return time.monotonic()
        if action is not None and time.monotonic() - last_action >= 0.08:
            action()
            last_action = time.monotonic()
        time.sleep(0.01)
    raise TimeoutError(description)


def command(argv, root, timeout=35):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    (root / "setup.log").open("a").write("$ " + " ".join(argv) + "\n" +
                                         result.stdout + result.stderr + "\n")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr[-1500:]}")
    return result.stdout


def start(argv, root, name, processes, env=None):
    log = (root / (name + ".log")).open("wb")
    process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                               env=env, start_new_session=True)
    processes[name] = process
    return process, log


def stop(process, force=False):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def sealed_incident_snapshot(log_path, snapshot_path):
    """Verify a stable sealed prefix while the live world stream continues."""
    entries = []
    for line in log_path.read_text().splitlines():
        try:
            entries.append((line, json.loads(line)))
        except json.JSONDecodeError:
            break  # A concurrent append may still be writing the final line.
    arm_incident = next((i for i, (_, row) in enumerate(entries)
                         if row["kind"] == "revoke" and
                         row["payload"].get("reason") == "arm:world-changed"), None)
    if arm_incident is None:
        return False
    sealed = next((i for i in range(len(entries) - 1, arm_incident, -1)
                   if entries[i][1]["kind"] == "seal"), None)
    if sealed is None:
        return False
    snapshot_path.write_text("\n".join(line for line, _ in entries[:sealed + 1]) + "\n")
    return True


def run(root, binary, live=None, wait_for_viewer=False, live_hold_seconds=0):
    root.mkdir(parents=True, exist_ok=True)
    fixture(root, binary, arm=True)
    params_path = root / "params.yaml"
    params = json.loads(params_path.read_text())
    params["haetae_gate"]["ros__parameters"]["use_sim_time"] = True
    params_path.write_text(json.dumps(params))
    controllers = HERE / "controllers.yaml"
    model = root / "reference_bot.urdf"
    model.write_text(command(["xacro", str(HERE / "reference_bot.urdf.xacro"),
                              "controllers_file:=" + str(controllers)], root))
    description_params = root / "robot_description.yaml"
    description_params.write_text(json.dumps({"robot_state_publisher": {"ros__parameters": {
        "robot_description": model.read_text(), "use_sim_time": True}}}))

    processes = {}
    logs = []
    world = None
    executor = None
    thread = None
    rclpy.init()
    try:
        gazebo_env = os.environ.copy()
        gazebo_env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = os.pathsep.join(filter(None, [
            "/opt/ros/jazzy/lib", gazebo_env.get("GZ_SIM_SYSTEM_PLUGIN_PATH", "")]))
        _, log = start(["gz", "sim", "-s", "-r", "-v", "2", "empty.sdf"],
                       root, "gazebo", processes, gazebo_env)
        logs.append(log)
        _, log = start(["ros2", "run", "ros_gz_bridge", "parameter_bridge",
                        "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"],
                       root, "clock_bridge", processes)
        logs.append(log)
        _, log = start(["ros2", "run", "robot_state_publisher", "robot_state_publisher",
                        "--ros-args", "--params-file", str(description_params)],
                       root, "robot_state_publisher", processes)
        logs.append(log)
        world = GazeboWorld(live)
        executor = MultiThreadedExecutor(num_threads=3)
        executor.add_node(world)
        thread = threading.Thread(target=executor.spin, daemon=True)
        thread.start()
        wait_for(lambda: world.get_clock().now().nanoseconds > 0, 30, processes, "Gazebo clock")
        command(["ros2", "run", "ros_gz_sim", "create", "-file", str(model),
                 "-name", "haetae_reference", "-x", "5", "-y", "5", "-z", "0"], root)
        for controller in ("joint_state_broadcaster", "diff_drive_base_controller",
                           "joint_trajectory_controller"):
            command(["ros2", "run", "controller_manager", "spawner", controller,
                     "--controller-manager-timeout", "30", "--param-file", str(controllers)], root)
        wait_for(lambda: world.world_count >= 3, 20, processes, "odom and shoulder feedback")

        _, log = start([sys.executable, str(REPO / "ros/haetae_gate/node.py"),
                        "--ros-args", "--params-file", str(params_path),
                        "-r", "/cmd_vel:=" + BASE_CONTROLLER_TOPIC],
                       root, "gate", processes)
        logs.append(log)
        wait_for(lambda: world.states and world.states[-1][1]["mode"] == "normal",
                 10, processes, "Haetae normal state")
        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "base rearm", action=lambda: world.propose_base(0.0))
        if live:
            world.marker("실험 준비 완료")
            if wait_for_viewer:
                wait_for(lambda: live.status()["viewers"] > 0, 120, processes,
                         "browser live viewer")
            world.marker("AI 바퀴 이동 명령")
        start_x = world.pose()[0]
        wait_for(lambda: world.pose()[0] >= start_x + 0.03 and world.speed() > 0.08,
                 8, processes, "approved base motion", action=lambda: world.propose_base(0.2))
        moving_x = world.pose()[0]
        revoked_at = time.monotonic()
        revoked_sim_ms = world.get_clock().now().nanoseconds // 1_000_000
        world.human = (moving_x + 0.1, world.pose()[1])
        world.marker("사람 등장")
        zero_at = wait_for(lambda: any(t >= revoked_at and value == 0 for t, value in world.commands),
                           3, processes, "world-triggered zero base command")
        world.marker("해태가 바퀴 0속도 명령")
        zero_sim_ms = world.get_clock().now().nanoseconds // 1_000_000
        wait_for(lambda: abs(world.speed()) < 0.03, 3, processes, "base stopped after human")
        base_stop_x = world.pose()[0]
        world.marker("Gazebo 바퀴 정지")
        if live:
            time.sleep(0.8)
        world.human = None
        time.sleep(0.3)

        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "arm rearm", action=lambda: world.propose_base(0.0))
        bad_at = time.monotonic()
        world.marker("AI 팔 범위 초과 명령")
        world.propose_arm(2.0)
        wait_for(lambda: any(t >= bad_at and value.get("decision", {}).get("verdict") == "bul"
                             for t, value in world.outcomes), 5, processes, "out-of-bounds arm denial")
        denied_position = world.shoulder()
        if abs(denied_position) > 0.02:
            raise AssertionError("denied arm command moved the joint")
        world.marker("해태가 팔 명령 거부")
        if live:
            time.sleep(0.8)
        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "arm rearm after denial", action=lambda: world.propose_base(0.0))
        world.marker("AI 정상 팔 이동 명령")
        world.propose_arm(0.12)
        wait_for(lambda: world.shoulder() > 0.01, 5, processes, "approved arm motion")
        arm_at = time.monotonic()
        world.human = (5.5, 5.5)
        world.marker("사람 등장, 팔 취소 요청")
        wait_for(lambda: any(t >= arm_at and value["arm_cancelling"]
                             for t, value in world.states), 3, processes, "arm cancellation request")
        wait_for(lambda: world.states and not world.states[-1][1]["arm_cancelling"],
                 3, processes, "arm cancelled result")
        arm_cancel_position = world.shoulder()
        time.sleep(0.35)
        if abs(world.shoulder() - arm_cancel_position) > 0.02:
            raise AssertionError("arm kept moving after cancellation")
        world.marker("Gazebo 팔 관절 정지")
        if live:
            time.sleep(0.8)
        world.human = None

        snapshot = root / "sealed-snapshot.jsonl"
        wait_for(lambda: sealed_incident_snapshot(root / "sillok.jsonl", snapshot),
                 3, processes, "sealed arm incident")
        report = subprocess.run([binary, "sillok", "verify", "--log", str(snapshot),
                                 "--pubkey", public(9)], capture_output=True, text=True)
        if report.returncode or not json.loads(report.stdout)["fully_sealed"]:
            raise AssertionError("incident log is not fully sealed")

        time.sleep(0.3)
        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "base rearm before kill", action=lambda: world.propose_base(0.0))
        wait_for(lambda: world.speed() > 0.08, 8, processes, "second base motion",
                 action=lambda: world.propose_base(0.2))
        killed_at = time.monotonic()
        killed_sim_ms = world.get_clock().now().nanoseconds // 1_000_000
        world.marker("해태 프로세스 강제 종료")
        stop(processes.pop("gate"), force=True)
        stopped_at = wait_for(lambda: abs(world.speed()) < 0.03, 3, processes,
                              "controller deadman after gate kill")
        stopped_sim_ms = world.get_clock().now().nanoseconds // 1_000_000
        world.marker("컨트롤러 데드맨으로 바퀴 정지")
        result = {"ok": True, "controller": "Gazebo Harmonic gz_ros2_control",
                  "base_moved_m": round(moving_x - start_x, 3),
                  "human_to_zero_wall_ms": round((zero_at - revoked_at) * 1000, 1),
                  "human_to_zero_sim_ms": zero_sim_ms - revoked_sim_ms,
                  "base_stop_distance_m": round(base_stop_x - moving_x, 3),
                  "arm_out_of_bounds_denied": True,
                  "arm_cancelled_at_rad": round(arm_cancel_position, 4),
                  "gate_kill_to_base_stop_wall_ms": round((stopped_at - killed_at) * 1000, 1),
                  "gate_kill_to_base_stop_sim_ms": stopped_sim_ms - killed_sim_ms,
                  "sillok_incident_snapshot_fully_sealed": True}
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        if live:
            live.publish({"kind": "result", "sim_ms": stopped_sim_ms, "result": result})
            if live_hold_seconds:
                time.sleep(live_hold_seconds)
        print(json.dumps(result))
    finally:
        for process in list(processes.values())[::-1]:
            stop(process)
        if executor is not None:
            executor.shutdown()
        if world is not None:
            world.destroy_node()
        rclpy.shutdown()
        if thread is not None:
            thread.join(timeout=2)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", help="path to the built haetae executable")
    parser.add_argument("--out", type=Path, help="directory for logs and result.json")
    parser.add_argument("--live-port", type=int, help="serve live Gazebo/ROS telemetry on loopback")
    parser.add_argument("--wait-for-viewer", action="store_true",
                        help="start motion after a browser connects to the live page")
    parser.add_argument("--live-hold-seconds", type=float, default=5,
                        help="keep the completed live view connected for this many seconds")
    args = parser.parse_args()
    if args.wait_for_viewer and args.live_port is None:
        parser.error("--wait-for-viewer requires --live-port")
    if args.live_port is not None and not 1 <= args.live_port <= 65535:
        parser.error("--live-port must be between 1 and 65535")
    if args.live_hold_seconds < 0:
        parser.error("--live-hold-seconds must be nonnegative")
    binary = str(Path(args.binary).resolve())
    live = LiveHub() if args.live_port is not None else None
    server = start_server(live, args.live_port) if live else None
    if server:
        print(f"Live view: http://127.0.0.1:{args.live_port}/", flush=True)
    shared = Path("/dev/shm")
    try:
        with tempfile.TemporaryDirectory(dir=shared if shared.is_dir() and os.access(shared, os.W_OK)
                                         else None) as directory:
            try:
                run(Path(directory), binary, live, args.wait_for_viewer,
                    args.live_hold_seconds if live else 0)
            finally:
                if args.out:
                    output = args.out.resolve()
                    output.mkdir(parents=True, exist_ok=True)
                    for name in ("result.json", "setup.log", "gazebo.log", "gate.log",
                                 "clock_bridge.log", "robot_state_publisher.log",
                                 "sillok.jsonl", "sealed-snapshot.jsonl", "reference_bot.urdf"):
                        source = Path(directory) / name
                        if source.exists():
                            shutil.copy2(source, output / name)
    finally:
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
