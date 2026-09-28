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


REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE_CONTROLLER_TOPIC = "/diff_drive_base_controller/cmd_vel"


class GazeboWorld(Node):
    def __init__(self):
        super().__init__("haetae_gazebo_world", parameter_overrides=[
            Parameter("use_sim_time", Parameter.Type.BOOL, True)],
            automatically_declare_parameters_from_overrides=True)
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
        self.create_subscription(String, "/haetae_gate/state",
                                 lambda m: self.states.append((time.monotonic(), json.loads(m.data))), 10)
        self.create_subscription(String, "/haetae_gate/outcome",
                                 lambda m: self.outcomes.append((time.monotonic(), json.loads(m.data))), 10)
        self.create_timer(0.05, self._publish_world)

    def _odom(self, msg):
        self.odom = msg
        self.odom_received = time.monotonic()

    def _joint(self, msg):
        if "shoulder" in msg.name:
            self.joint = msg
            self.joint_received = time.monotonic()

    def _command(self, msg):
        self.commands.append((time.monotonic(), msg.twist.linear.x))

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


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def run(root, binary):
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
        world = GazeboWorld()
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
        start_x = world.pose()[0]
        wait_for(lambda: world.pose()[0] >= start_x + 0.03 and world.speed() > 0.08,
                 8, processes, "approved base motion", action=lambda: world.propose_base(0.2))
        moving_x = world.pose()[0]
        revoked_at = time.monotonic()
        world.human = (moving_x + 0.1, world.pose()[1])
        zero_at = wait_for(lambda: any(t >= revoked_at and value == 0 for t, value in world.commands),
                           3, processes, "world-triggered zero base command")
        wait_for(lambda: abs(world.speed()) < 0.03, 3, processes, "base stopped after human")
        base_stop_x = world.pose()[0]
        world.human = None
        time.sleep(0.3)

        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "arm rearm", action=lambda: world.propose_base(0.0))
        bad_at = time.monotonic()
        world.propose_arm(2.0)
        wait_for(lambda: any(t >= bad_at and value.get("decision", {}).get("verdict") == "bul"
                             for t, value in world.outcomes), 5, processes, "out-of-bounds arm denial")
        denied_position = world.shoulder()
        if abs(denied_position) > 0.02:
            raise AssertionError("denied arm command moved the joint")
        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "arm rearm after denial", action=lambda: world.propose_base(0.0))
        world.propose_arm(0.12)
        wait_for(lambda: world.shoulder() > 0.01, 5, processes, "approved arm motion")
        arm_at = time.monotonic()
        world.human = (5.5, 5.5)
        wait_for(lambda: any(t >= arm_at and value["arm_cancelling"]
                             for t, value in world.states), 3, processes, "arm cancellation request")
        wait_for(lambda: world.states and not world.states[-1][1]["arm_cancelling"],
                 3, processes, "arm cancelled result")
        arm_cancel_position = world.shoulder()
        time.sleep(0.35)
        if abs(world.shoulder() - arm_cancel_position) > 0.02:
            raise AssertionError("arm kept moving after cancellation")
        world.human = None

        report = subprocess.run([binary, "sillok", "verify", "--log", str(root / "sillok.jsonl"),
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
        stop(processes.pop("gate"))
        stopped_at = wait_for(lambda: abs(world.speed()) < 0.03, 3, processes,
                              "controller deadman after gate kill")
        result = {"ok": True, "controller": "Gazebo Harmonic gz_ros2_control",
                  "base_moved_m": round(moving_x - start_x, 3),
                  "human_to_zero_ms": round((zero_at - revoked_at) * 1000, 1),
                  "base_stop_distance_m": round(base_stop_x - moving_x, 3),
                  "arm_out_of_bounds_denied": True,
                  "arm_cancelled_at_rad": round(arm_cancel_position, 4),
                  "gate_kill_to_base_stop_ms": round((stopped_at - killed_at) * 1000, 1),
                  "sillok_fully_sealed": True}
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
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
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    if args.out:
        run(args.out.resolve(), binary)
    else:
        shared = Path("/dev/shm")
        with tempfile.TemporaryDirectory(dir=shared if shared.is_dir() and os.access(shared, os.W_OK)
                                         else None) as directory:
            run(Path(directory), binary)


if __name__ == "__main__":
    main()
