#!/usr/bin/env python3
"""Exercise Haetae against Gazebo Harmonic and real ros2_control controllers.

This is a physics-backed reference test, not a robot safety certification.
Person occupancy comes from the native Gazebo GPU lidar; robot feedback,
Gazebo Transport and ros2_control are trusted. The four-joint controller checks an independent 250 ms gateway lease.
"""

import argparse
from collections import deque
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
import traceback
from xml.etree import ElementTree

import rclpy
from geometry_msgs.msg import TwistStamped
from nav_msgs.msg import Odometry
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "haetae_sim"))
from run_scenario import fixture, public  # noqa: E402
from network_guard import NetworkGuard, sandboxed
from transport_probe import probe_transport
from compound_fault import exercise_compound
from artifact_export import export_artifacts
from public_report import report as public_report
from live_stream import LiveHub, start_server  # noqa: E402
from scene_layout import nearby_person, person_entry, PersonWalk  # noqa: E402
from product_model import ARM_JOINTS, MODEL_NAME, PERSON_DISTANCE_M, arm_policy, resolve_meshes  # noqa: E402
from role_isolation import Roles, UIDS
from lidar_perception import Perception
from native_person import NativeScene, add_native_scene
from stop_evidence import person_stop_report, person_stop_observed, world_expiry_stop_observed  # noqa: E402
from attack_probe import (prepare_gazebo_security, probe_gazebo_permissions,
                          probe_permissions, probe_signed_inputs)  # noqa: E402


REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE_CONTROLLER_TOPIC = "/diff_drive_base_controller/cmd_vel"


class RunInterrupted(Exception):
    """Operator requested container shutdown, distinct from a failed test."""


def interrupt_run(signum, frame):
    # A second TERM must not interrupt cleanup or the final artifact checkpoint.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise RunInterrupted("operator requested shutdown")


class GazeboWorld(Node):
    def __init__(self, live=None, isolated=False):
        super().__init__("haetae_gazebo_world", parameter_overrides=[
            Parameter("use_sim_time", Parameter.Type.BOOL, True)],
            automatically_declare_parameters_from_overrides=True)
        self.live = live
        self.proposal_pipe = None
        self.isolated = isolated
        self.odom = None
        self.joint = None
        self.odom_received = 0.0
        self.joint_received = 0.0
        self.perception = Perception()
        self.native = None
        self.sensor_info = {}
        self.human = None
        self.human_walk = None
        self.human_motion = None
        self.human_lock = threading.RLock()
        self.person_reports = deque(maxlen=512)
        self.zero_commands = deque(maxlen=512)
        self.states = []
        self.outcomes = []
        self.commands = []
        self.guard_states = []
        self.attack_world_received = 0
        self.world_count = 0
        self.world_pub = self.create_publisher(String,
            "/haetae_input/world" if isolated else "/haetae_gate/world", 1)
        self.vla_pub = None if isolated else self.create_publisher(TwistStamped, "/vla/cmd_vel", 1)
        self.arm_pub = None if isolated else self.create_publisher(JointTrajectory, "/vla/arm", 1)
        self.create_subscription(Odometry, "/diff_drive_base_controller/odom", self._odom, 10)
        self.create_subscription(JointState, "/joint_states", self._joint, 10)
        self.create_subscription(TwistStamped, BASE_CONTROLLER_TOPIC, self._command, 10)
        self.create_subscription(String, "/haetae_gate/state", self._state, 10)
        self.create_subscription(String, "/haetae_gate/outcome", self._outcome, 10)
        self.create_subscription(String, "/haetae_input/world" if isolated else "/haetae_gate/world", self._observe_world_attack, 10)
        self.create_subscription(String, "/joint_trajectory_controller/guard_state",
                                 lambda msg: self.guard_states.append((time.monotonic(), json.loads(msg.data))), 10)
        self.create_timer(0.05, self._publish_world)

    def _emit(self, kind, **values):
        if self.live:
            self.live.publish({"kind": kind,
                               "sim_ms": self.get_clock().now().nanoseconds // 1_000_000,
                               **values})

    def marker(self, label, **values):
        self._emit("phase", label=label, **values)

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

    def _observe_world_attack(self, msg):
        try:
            if json.loads(msg.data).get("confidence") == 0.314159:
                self.attack_world_received += 1
        except (ValueError, TypeError):
            pass

    def _odom(self, msg):
        self.odom = msg
        self.odom_received = time.monotonic()

    def _joint(self, msg):
        if all(name in msg.name for name in ARM_JOINTS):
            self.joint = msg
            self.joint_received = time.monotonic()

    def _command(self, msg):
        now = time.monotonic()
        self.commands.append((now, msg.twist.linear.x))
        if msg.twist.linear.x == 0:
            self.zero_commands.append((now, self.get_clock().now().nanoseconds // 1_000_000))
        self._emit("base_command", linear=msg.twist.linear.x)

    def pose(self):
        if self.odom is None:
            return None
        p = self.odom.pose.pose.position
        # The odom frame starts at the model's spawn pose (5, 5).
        return (5.0 + p.x, 5.0 + p.y)

    def primary_joint(self):
        if self.joint is None:
            return None
        return self.joint.position[list(self.joint.name).index(ARM_JOINTS[0])]

    def arm_positions(self):
        return [self.joint.position[list(self.joint.name).index(name)] for name in ARM_JOINTS]

    def measured_joints(self):
        return [{"name": name, "position": self.joint.position[i],
                 "velocity": self.joint.velocity[i] if i < len(self.joint.velocity) else 0.0}
                for i, name in enumerate(self.joint.name)]

    def speed(self):
        return 0.0 if self.odom is None else self.odom.twist.twist.linear.x

    def heading(self):
        q = self.odom.pose.pose.orientation
        return math.atan2(2 * (q.w * q.z + q.x * q.y),
                          1 - 2 * (q.y * q.y + q.z * q.z))

    def begin_person_walk(self, start, end, speed=0.24):
        with self.human_lock:
            stamp = self.get_clock().now().nanoseconds // 1_000_000
            distance = self.human_motion["distance_m"] if self.human_motion else 0.0
            self.human_walk = PersonWalk(start, end, stamp, speed, distance)
            self.human, self.human_motion = self.human_walk.sample(stamp)

    def desired_person(self):
        with self.human_lock:
            if self.human_walk:
                stamp = self.get_clock().now().nanoseconds // 1_000_000
                self.human, self.human_motion = self.human_walk.sample(stamp)
            return self.human, self.human_motion

    def person_walk_finished(self):
        with self.human_lock:
            return self.human_walk is None or not self.human_motion["moving"]

    def freeze_person(self):
        with self.human_lock:
            self.human_walk = None
            if self.human_motion:
                self.human_motion = {**self.human_motion, "moving": False, "speed_mps": 0.0}

    def remove_person(self):
        with self.human_lock:
            self.human = self.human_walk = self.human_motion = None

    def _publish_world(self):
        now = time.monotonic()
        stamp = self.get_clock().now().nanoseconds // 1_000_000
        if (not stamp or self.odom is None or self.joint is None or
                now - self.odom_received > 0.2 or now - self.joint_received > 0.2):
            return
        joint_stamp = (self.joint.header.stamp.sec * 1000 +
                       self.joint.header.stamp.nanosec // 1_000_000)
        odom_stamp = (self.odom.header.stamp.sec * 1000 +
                      self.odom.header.stamp.nanosec // 1_000_000)
        if not (-20 <= stamp - joint_stamp < 200 and -20 <= stamp - odom_stamp < 200):
            return
        # Never restamp an older joint sample as current: arm tracking uses
        # this measurement time, not the timer's publication time.
        stamp = joint_stamp
        yaw = self.heading()
        joints = self.measured_joints()
        x, y = self.pose()
        humans, confidence, sensor = self.perception.snapshot(
            self.get_clock().now().nanoseconds // 1_000_000, (x, y))
        self.sensor_info = sensor
        # Fuse at the oldest source timestamp; never disguise a stale scan as
        # a new world. Joint/odom freshness was checked above independently.
        if sensor["healthy"]:
            stamp = min(stamp, odom_stamp, sensor["stamp_ms"])
        with self.human_lock:
            human_motion = self.human_motion
            present = self.human is not None
        observed, native_stamp = self.native.observed_sample() if self.native else (None, None)
        render_humans = ([{"id": "sim-person", "class": "adult",
                          "pos": {"x": observed[0], "y": observed[1]}}]
                         if present and observed and observed[2] > 0 else [])
        payload = {"stamp_ms": stamp,
                   "robot": {"pose": {"x": x, "y": y}, "yaw": yaw,
                             "twist": {"linear": self.speed(), "angular": self.odom.twist.twist.angular.z},
                             "joints": [j for name in ARM_JOINTS for j in joints if j["name"] == name]},
                   "humans": humans,
                   "confidence": confidence}
        if confidence and humans:
            nearest = min(humans, key=lambda h: math.hypot(h["pos"]["x"]-x, h["pos"]["y"]-y))
            self.person_reports.append({"wall": time.monotonic(), "stamp_ms": stamp,
                                        "robot": (x, y), "human": (nearest["pos"]["x"], nearest["pos"]["y"])})
        if confidence:
            self.world_pub.publish(String(data=json.dumps(payload)))
            self.world_count += 1
        # Unknown coverage is visible in telemetry, but cannot refresh the
        # enforcer world: its original 200 ms source-age stop remains active.
        self._emit("telemetry", sim_ms=joint_stamp, x=x, y=y, speed=self.speed(),
                   joint=self.primary_joint(), joints=joints, yaw=yaw, humans=render_humans,
                   detections=humans, sensor=sensor,
                   native_person_stamp_ms=native_stamp,
                   human_motion=human_motion, model=MODEL_NAME)

    def propose_base(self, linear):
        if self.isolated:
            self.proposal_pipe.write(json.dumps({"base": linear}).encode() + b"\n")
            self.proposal_pipe.flush()
            return
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.twist.linear.x = linear
        self.vla_pub.publish(msg)

    def propose_arm(self, target):
        start = self.arm_positions()
        end = [target, *start[1:]]
        if self.isolated:
            self.proposal_pipe.write(json.dumps({"joints": list(ARM_JOINTS),
                "points": [(0, start), (800, end), (1000, end)]}).encode() + b"\n")
            self.proposal_pipe.flush()
            return
        msg = JointTrajectory()
        msg.joint_names = list(ARM_JOINTS)
        for millis, value in ((0, start), (800, end), (1000, end)):
            p = JointTrajectoryPoint()
            p.positions = value
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


def check_person_sensor(world, label, processes):
    def detected():
        frame = world.perception.frame
        pose, _ = world.native.observed_sample()
        desired = world.human
        return (frame and frame.healthy and len(frame.points) >= 3 and pose and desired
                and math.dist(pose[:2], desired) < 0.025
                and min(math.dist(point, pose[:2]) for point in frame.points) < 0.25)
    try:
        wait_for(detected, 3, processes, "native person and lidar agreement")
    except TimeoutError as exc:
        raise AssertionError("native person / lidar disagreement: " + json.dumps({
            "native_pose": world.native.observed_sample(), "desired": world.human,
            "sensor": world.sensor_info, "driver_error": world.native.error})) from exc
    frame = world.perception.frame
    pose, _ = world.native.observed_sample()
    return {"case": label, "input": "gazebo_gpu_lidar", "sensor_stamp_ms": frame.stamp_ms,
            "native_torso": list(pose), "test_path": list(world.human),
            "surface_to_torso_m": round(min(math.dist(point, pose[:2]) for point in frame.points), 4),
            "returns": len(frame.points), "ok": True}


def exercise_sensor_fault(world, processes, case):
    world.marker("센서 연결 끊김 시험" if case == "disconnect" else "센서 검증 표적 사라짐")
    wait_for(lambda: world.sensor_info.get("healthy") and not world.perception.snapshot(
        world.get_clock().now().nanoseconds // 1_000_000, world.pose())[0],
        5, processes, "fresh empty calibrated bay")
    world.states.clear()
    world.outcomes.clear()
    wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"] and
             sum(1 for _, row in world.outcomes if row.get("decision", {}).get("verdict") == "yun"
                 and row.get("decision", {}).get("action", {}).get("type") == "stop") >= 2,
             8, processes, "sensor fixture explicit rearm", action=lambda: world.propose_base(0.0))
    wait_for(lambda: world.speed() > 0.06, 5, processes, "sensor fixture actual motion",
             action=lambda: world.propose_base(0.12))
    fault_at = time.monotonic()
    last_sensor_ms = world.sensor_info["stamp_ms"]
    if case == "disconnect":
        with world.perception.lock:
            world.perception.drop_frames = True
    elif not world.native.calibration_visible(False):
        raise AssertionError("could not remove native calibration target")
    wait_for(lambda: not world.sensor_info.get("healthy") and world_expiry_stop_observed(
        world.states, world.outcomes, fault_at) and abs(world.speed()) < 0.03,
             3, processes, "sensor fault causes stale-world stop",
             action=lambda: world.propose_base(0.12))
    zero_at = next(t for t, _ in world.zero_commands if t >= fault_at)
    unknown = dict(world.sensor_info)
    if unknown["healthy"] or zero_at - fault_at > 0.4:
        raise AssertionError("sensor fault " + case + " zero_ms=" + str(round((zero_at-fault_at)*1000,1)) + " unknown=" + json.dumps(unknown))
    if case == "disconnect":
        with world.perception.lock:
            world.perception.drop_frames = False
    elif not world.native.calibration_visible(True):
        raise AssertionError("could not restore native calibration target")
    wait_for(lambda: world.sensor_info.get("healthy"), 5, processes, "sensor recovers")
    # Sensor recovery alone cannot reactivate a disarmed motion source.
    world.propose_base(0.12)
    time.sleep(0.3)
    if abs(world.speed()) >= 0.03 or "vla" in world.states[-1][1]["armed"]:
        raise AssertionError("sensor recovery automatically rearmed motion")
    result = {"ok": True, "case": case, "input": "gazebo_gpu_lidar",
              "last_sensor_ms": last_sensor_ms, "unknown": unknown,
              "fault_to_zero_wall_ms": round((zero_at-fault_at)*1000, 1),
              "stop_reason": world_expiry_stop_observed(world.states, world.outcomes, fault_at),
              "recovery_did_not_rearm": True}
    world._emit("sensor_fault_result", **result)
    world.marker("센서 이상 → 정지")
    return result


def command(argv, root, timeout=35, env=None):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    (root / "setup.log").open("a").write("$ " + " ".join(argv) + "\n" +
                                         result.stdout + result.stderr + "\n")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr[-1500:]}")
    return result.stdout


def start(argv, root, name, processes, env=None, user=None, input_pipe=False):
    log = (root / (name + ".log")).open("wb")
    options = {"user": user, "group": user, "extra_groups": []} if user is not None else {}
    process = subprocess.Popen(sandboxed(argv) if user is not None else argv, stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                               stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                               env=env, start_new_session=True, **options)
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


def exercise_arm_fault(world, processes, case):
    """Fault injection is confined to this test harness, never the gate."""
    time.sleep(0.3)  # Drain the rearm stop proposals before submitting motion.
    wait_for(lambda: world.guard_states and not world.guard_states[-1][1]["holding"],
             5, processes, "independent controller lease opens")
    initial = world.arm_positions()
    world.marker("팔 독립 정지 시험", case=case)
    world.propose_arm(initial[0] + (-0.5 if initial[0] > 0.1 else 0.5))
    wait_for(lambda: abs(world.primary_joint() - initial[0]) > 0.10, 5, processes,
             "arm moving before injected fault")
    gate = processes.pop("gate")
    fault_wall = time.monotonic()
    fault_sim = world.get_clock().now().nanoseconds // 1_000_000
    world.marker("팔 동작 중 연결 고장", case=case)
    if case == "kill":
        stop(gate, force=True)
    elif case == "delay":
        os.kill(gate.pid, signal.SIGSTOP)
    else:
        children = Path(f"/proc/{gate.pid}/task/{gate.pid}/children").read_text().split()
        if len(children) != 1:
            raise AssertionError("cannot identify the Rust enforcement child")
        os.kill(int(children[0]), signal.SIGSTOP)
    try:
        stopped = wait_for(lambda: world.guard_states and world.guard_states[-1][0] >= fault_wall
                           and world.guard_states[-1][1]["holding"], 2, processes,
                           "controller independently expires its lease")
        stop_sim = world.guard_states[-1][1]["cutoff_ms"]
        controller_stop_wall = world.guard_states[-1][1]["stop_wall_ns"] / 1_000_000_000
        if stop_sim-fault_sim > 320 or controller_stop_wall-fault_wall > 0.4:
            raise AssertionError("controller missed its independent stop deadline")
        wait_for(lambda: world.joint.header.stamp.sec * 1000 +
                 world.joint.header.stamp.nanosec // 1_000_000 >= stop_sim and
                 all(abs(world.joint.velocity[list(world.joint.name).index(j)]) < 0.03
                     for j in ARM_JOINTS), 2, processes, "fresh stopped-joint feedback")
        positions = world.arm_positions()
        time.sleep(0.4)
        drift = max(abs(a-b) for a,b in zip(positions, world.arm_positions()))
        if drift > 0.02:
            raise AssertionError(f"arm moved {drift} rad after independent stop")
        world.marker("팔 제어기가 스스로 정지", case=case)
        # A delayed gateway is resumed only to test queued-message rejection.
        # No new proposal is sent. Its old action must never resume.
        if case == "delay":
            os.kill(gate.pid, signal.SIGCONT)
            time.sleep(0.5)
            if max(abs(a-b) for a,b in zip(positions, world.arm_positions())) > 0.02:
                raise AssertionError("heartbeat recovery revived the expired trajectory")
        return {"ok": True, "case": case, "controller": "independent_arm_lease",
                "joint_names": list(ARM_JOINTS), "held_positions_rad": positions,
                "post_stop_drift_rad": drift,
                "fault_to_hold_wall_ms": round((controller_stop_wall-fault_wall)*1000, 1),
                "hold_observed_after_wall_ms": round((stopped-fault_wall)*1000, 1),
                "fault_to_hold_sim_ms": stop_sim-fault_sim,
                "old_goal_did_not_resume": True}
    finally:
        if gate.poll() is None:
            os.killpg(gate.pid, signal.SIGCONT)
        stop(gate, force=True)


def run(root, binary, live=None, wait_for_viewer=False, live_hold_seconds=0,
        gazebo_gui=False, manual_start=False, attack_probes=False,
        secure_graph=False, step_through=False, arm_fault=None, compound_repeat=0, output=None):
    root.mkdir(parents=True, exist_ok=True)
    fixture(root, binary, arm=True, arm_policy=arm_policy(), person_distance=PERSON_DISTANCE_M)
    params_path = root / "params.yaml"
    params = json.loads(params_path.read_text())
    params["haetae_gate"]["ros__parameters"].update({"use_sim_time": True,
        "heartbeat_topic": "/haetae_gate/heartbeat"})
    params_path.write_text(json.dumps(params))
    studio = ElementTree.parse(HERE / "studio.sdf")
    add_native_scene(studio.getroot().find("world"))
    studio_path = root / "sensor-studio.sdf"
    studio.write(studio_path, encoding="UTF-8", xml_declaration=True)
    controllers = HERE / "controllers.yaml"
    model = root / "reference_bot.urdf"
    model.write_text(resolve_meshes(command(["xacro", str(HERE / "rosbot_xl.urdf.xacro"),
                              "controllers_file:=" + str(controllers),
                              "vendor_dir:=" + str(HERE / "vendor")], root)))
    description_params = root / "robot_description.yaml"
    description_params.write_text(json.dumps({"robot_state_publisher": {"ros__parameters": {
        "robot_description": model.read_text(), "use_sim_time": True}}}))

    processes = {}
    logs = []
    world = None
    executor = None
    thread = None
    roles = None
    network_guard = None
    transport_evidence = None
    gate_directory = root
    if secure_graph:
        keystore = prepare_gazebo_security(root / "gazebo-security")
        os.environ.update({"ROS_SECURITY_ENABLE": "true",
                           "ROS_SECURITY_STRATEGY": "Enforce",
                           "ROS_SECURITY_KEYSTORE": str(keystore),
                           "ROS_SECURITY_ENCLAVE_OVERRIDE": "/haetae/world",
                           "FASTDDS_BUILTIN_TRANSPORTS": "UDPv4"})
        roles = Roles(root, keystore, binary, ARM_JOINTS)
        gate_directory = roles.directories["gate"] / root.name
        params_path = gate_directory / "params.yaml"
        (root / "principal-isolation.json").write_text(json.dumps(roles.probe_read_boundaries(), indent=2))

    def role_env(role):
        env = os.environ.copy()
        if secure_graph:
            env["ROS_SECURITY_ENCLAVE_OVERRIDE"] = "/haetae/" + role
            if roles and role in UIDS:
                return roles.environment(role, env)
        return env

    def presentation_wait(seconds):
        if live:
            until = time.monotonic() + seconds
            wait_for(lambda: time.monotonic() >= until, seconds + 2,
                     processes, "scene presentation")

    def review_scene(label, detail, next_label):
        if not live:
            return
        # Only call after the controller has stopped. ROS callbacks, physics
        # and watchdogs continue while the viewer reads the result.
        if abs(world.speed()) >= 0.03:
            raise AssertionError("cannot wait for viewer while base is moving")
        if step_through:
            token = live.begin_checkpoint(label=label, detail=detail, next_label=next_label,
                                          sim_ms=world.get_clock().now().nanoseconds // 1_000_000)
            wait_for(lambda: not live.checkpoint_active(token), 3600, processes,
                     "viewer next step")
        else:
            presentation_wait(4)

    def prepare_scene(detail):
        if live:
            world.marker("다음 장면 준비", detail=detail)
            presentation_wait(2.5)

    def freeze_native_person():
        world.freeze_person()
        # The path agreement oracle allows normal measurement latency while
        # walking. A user checkpoint also requires the native body to finish
        # reaching the frozen target, so it cannot drift during the hold.
        def settled():
            pose, _ = world.native.observed_sample()
            return pose and world.human and math.dist(pose[:2], world.human) < 1e-6
        wait_for(settled, 3, processes, "native person settles before checkpoint")

    sensor_person_evidence = []

    def clear_person():
        if live and world.human is not None:
            world.marker("사람이 걸어 나갑니다", detail="정지 장면 확인이 끝났습니다. 사람의 이동이 끝나면 다음 시험을 준비합니다.")
            world.begin_person_walk(world.human, person_entry(*world.pose(), world.heading()), speed=0.24)
            wait_for(world.person_walk_finished, 20, processes, "person exit path")
            freeze_native_person()
        if world.human is not None:
            sensor_person_evidence.append(check_person_sensor(world, "before_departure", processes))
        world.remove_person()
        wait_for(lambda: world.native.observed and world.native.observed[2] < 0 and
                 world.sensor_info.get("healthy") and not world.perception.snapshot(
                     world.get_clock().now().nanoseconds // 1_000_000, world.pose())[0],
                 5, processes, "native person departed and measured bay cleared")
        sensor_person_evidence.append({"case": "departure", "ok": True,
                                       "native_person_parked": True, "fresh_empty_scan": True})
        if live:
            world.marker("사람 보고 해제", detail="방금 장면의 사람 근접 보고를 해제했습니다. 다음 시험을 준비합니다.")
            presentation_wait(2)

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    try:
        if roles:
            network_guard = NetworkGuard(os.environ.get("ROS_DOMAIN_ID", "0"))
        if not os.environ.get("DISPLAY"):
            os.environ["DISPLAY"] = ":99"
            _, log = start(["Xvfb", ":99", "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                           root, "sensor_display", processes)
            logs.append(log)
            wait_for(lambda: Path("/tmp/.X11-unix/X99").exists(), 5, processes, "sensor render display")
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        # Mesa otherwise starts a worker pool sized to the host CPU count,
        # oversubscribing container quotas and competing with sensor/ROS work.
        os.environ["LP_NUM_THREADS"] = "2"
        gazebo_env = role_env("sim")
        gazebo_env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = os.pathsep.join(filter(None, [
            "/opt/haetae/lib", "/opt/ros/jazzy/lib", gazebo_env.get("GZ_SIM_SYSTEM_PLUGIN_PATH", "")]))
        _, log = start(["gz", "sim", "-s", "-r", "-v", "2", str(studio_path)],
                       root, "gazebo", processes, gazebo_env)
        logs.append(log)
        _, log = start(["ros2", "run", "ros_gz_bridge", "parameter_bridge",
                        "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"],
                       root, "clock_bridge", processes, gazebo_env)
        logs.append(log)
        _, log = start(["ros2", "run", "robot_state_publisher", "robot_state_publisher",
                        "--ros-args", "--params-file", str(description_params)],
                       root, "robot_state_publisher", processes, gazebo_env)
        logs.append(log)
        world = GazeboWorld(live, isolated=secure_graph)
        executor = MultiThreadedExecutor(num_threads=3)
        executor.add_node(world)
        thread = threading.Thread(target=executor.spin, daemon=True)
        thread.start()
        wait_for(lambda: world.get_clock().now().nanoseconds > 0, 30, processes, "Gazebo clock")
        world.native = NativeScene(world.desired_person, world.perception)
        if gazebo_gui:
            _, log = start(["gz", "sim", "-g", "-v", "2", "--gui-config",
                            str(HERE / "viewer.config")], root, "gazebo_gui", processes,
                           gazebo_env)
            logs.append(log)
        command(["ros2", "run", "ros_gz_sim", "create", "-file", str(model),
                 "-name", "haetae_reference", "-x", "5", "-y", "5", "-z", "0"], root,
                env=gazebo_env)
        for controller in ("joint_state_broadcaster", "diff_drive_base_controller",
                           "joint_trajectory_controller", "gripper_hold_controller"):
            command(["ros2", "run", "controller_manager", "spawner", controller,
                     "--controller-manager-timeout", "30", "--param-file", str(controllers)],
                    root, env=gazebo_env)
        wait_for(lambda: world.world_count >= 3 and world.sensor_info.get("healthy"),
                 30, processes, "lidar, odom and four-joint feedback")

        if roles:
            transport_evidence = probe_transport(world, network_guard, processes, wait_for)
            (root / "transport-isolation.json").write_text(json.dumps(transport_evidence, indent=2))
            for role in ("world", "vla"):
                _, log = start([sys.executable, str(REPO / "ros/haetae_gate/source_node.py"),
                                "--ros-args", "--params-file", str(roles.source_params(role))],
                               root, "source_" + role, processes, role_env(role), UIDS[role])
                logs.append(log)
            driver, log = start([sys.executable, str(HERE / "scenario_source.py")],
                                root, "scenario_vla", processes, role_env("proposal"),
                                UIDS["proposal"], input_pipe=True)
            world.proposal_pipe = driver.stdin
            logs.append(log)

        _, log = start([sys.executable, str(REPO / "ros/haetae_gate/node.py"),
                        "--ros-args", "--params-file", str(params_path),
                        "-r", "/cmd_vel:=" + BASE_CONTROLLER_TOPIC],
                       root, "gate", processes, role_env("gate"), UIDS["gate"] if roles else None)
        logs.append(log)
        wait_for(lambda: world.states and world.states[-1][1]["mode"] == "normal"
                 and world.states[-1][1].get("arm_controller_ready"),
                 10, processes, "Haetae normal state")
        if roles:
            graph_boundaries = roles.probe_graph_boundaries(os.environ.copy())
            (root / "role-permissions.json").write_text(json.dumps(graph_boundaries, indent=2))
            wait_for(lambda: (roles.directories["vla"] / "counters.json").exists(),
                     5, processes, "VLA source counter reservation",
                     action=lambda: world.propose_base(0.0))
            before = json.loads((roles.directories["vla"] / "counters.json").read_text())["counters"]["vla"]
            stop(processes.pop("source_vla"))
            _, log = start([sys.executable, str(REPO / "ros/haetae_gate/source_node.py"),
                            "--ros-args", "--params-file", str(roles.source_params("vla"))],
                           root, "source_vla", processes, role_env("vla"), UIDS["vla"])
            logs.append(log)
            wait_for(lambda: json.loads((roles.directories["vla"] / "counters.json").read_text())["counters"]["vla"] > before,
                     8, processes, "restarted signer advances reserved counter",
                     action=lambda: world.propose_base(0.0))
            (root / "source-restart.json").write_text(json.dumps({"role": "vla",
                "before": before, "after": json.loads((roles.directories["vla"] / "counters.json").read_text())["counters"]["vla"],
                "continued_counter": True}, indent=2))
        # Counter reservation happens before DDS delivery. After a signer
        # restart an old armed-state sample cannot confirm a new rearm.
        world.states.clear()
        world.outcomes.clear()
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"]
                 and sum(1 for _, row in world.outcomes
                         if row.get("decision", {}).get("verdict") == "yun"
                         and row.get("decision", {}).get("action", {}).get("type") == "stop") >= 2,
                 8, processes, "fresh accepted base rearm", action=lambda: world.propose_base(0.0))
        if arm_fault:
            result = exercise_arm_fault(world, processes, arm_fault)
            if roles and (gate_directory / "sillok.jsonl").exists():
                shutil.copy2(gate_directory / "sillok.jsonl", root / "sillok.jsonl")
            (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
            return
        if live:
            world.marker("실험 준비 완료")
            if wait_for_viewer:
                wait_for(lambda: live.status()["viewers"] > 0, 3600, processes,
                         "browser live viewer")
            if manual_start:
                world.marker("3D 화면 준비 · 시작 버튼을 누르세요")
                wait_for(lambda: live.start_requested.is_set(), 3600, processes,
                         "browser start command")
            world.marker("AI 바퀴 이동 명령")
        start_x = world.pose()[0]
        visible_motion = 0.35 if live else 0.03
        motion_at = time.monotonic()
        wait_for(lambda: world.pose()[0] >= start_x + visible_motion and world.speed() > 0.08
                 and (not live or time.monotonic() - motion_at >= 3),
                 12, processes, "approved base motion",
                 action=lambda: world.propose_base(0.12 if live else 0.2))
        moving_x = world.pose()[0]
        entry_at = time.monotonic()
        entry_sim_ms = world.joint.header.stamp.sec * 1000 + world.joint.header.stamp.nanosec // 1_000_000
        if live:
            world.begin_person_walk(person_entry(*world.pose(), world.heading()),
                                    nearby_person(*world.pose(), world.heading()))
            world.marker("사람이 걸어 접근합니다")
            # Keep submitting approved motion during the approach. A denied
            # proposal cannot rearm the robot: only a zero proposal can do so.
            # A world update can revoke active motion without a Decision
            # outcome. Require the causal engine record and observed zero.
            wait_for(lambda: any(t >= entry_at for t, _ in list(world.zero_commands)) and
                               person_stop_observed(gate_directory / "sillok.jsonl", world.person_reports,
                                                    world.zero_commands, entry_sim_ms),
                               20, processes, "walking person triggers base stop",
                               action=lambda: world.propose_base(0.12))
            freeze_native_person()
        else:
            world.human = nearby_person(*world.pose(), world.heading())
            world.marker("사람 등장")
            wait_for(lambda: person_stop_observed(gate_directory / "sillok.jsonl", world.person_reports,
                                                 world.zero_commands, entry_sim_ms),
                     5, processes, "native lidar person causes base stop",
                     action=lambda: world.propose_base(0.2))
        wait_for(lambda: person_stop_report(gate_directory / "sillok.jsonl", world.person_reports, entry_sim_ms),
                 3, processes, "recorded world sample causing person denial")
        trigger = person_stop_report(gate_directory / "sillok.jsonl", world.person_reports, entry_sim_ms)
        revoked_at, revoked_sim_ms = trigger["wall"], trigger["stamp_ms"]
        moving_x = trigger["robot"][0]
        zero_at, zero_sim_ms = next((t, stamp) for t, stamp in world.zero_commands if t >= revoked_at)
        world.marker("해태가 바퀴 0속도 명령")
        wait_for(lambda: abs(world.speed()) < 0.03, 3, processes, "base stopped after human")
        sensor_person_evidence.append(check_person_sensor(world, "base_stop", processes))
        base_stop_x = world.pose()[0]
        world.marker("Gazebo 바퀴 정지")
        review_scene("바퀴 정지 장면", "사람 근접 보고를 받고 바퀴가 멈췄습니다. 사람 표시와 정지 상태를 천천히 확인하세요.",
                     "팔 범위 제한 시험")
        clear_person()
        time.sleep(0.3)

        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "arm rearm", action=lambda: world.propose_base(0.0))
        prepare_scene("다음은 허용 범위를 넘는 팔 명령입니다. 팔이 움직이지 않는지 확인하세요.")
        bad_at = time.monotonic()
        denied_start = world.arm_positions()
        world.marker("AI 팔 범위 초과 명령")
        world.propose_arm(4.0)
        wait_for(lambda: any(t >= bad_at and "envelope:arm-position" in value.get("decision", {}).get("fired", [])
                             for t, value in world.outcomes), 5, processes, "out-of-bounds arm denial")
        if max(abs(a - b) for a, b in zip(world.arm_positions(), denied_start)) > 0.02:
            raise AssertionError("denied arm command moved the joint")
        world.marker("해태가 팔 명령 거부")
        review_scene("팔 명령 거부 장면", "허용 범위를 넘는 명령을 거부했고 팔 관절이 움직이지 않았습니다.",
                     "움직이는 팔 중단 시험")
        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "arm rearm after denial", action=lambda: world.propose_base(0.0))
        time.sleep(0.3)
        prepare_scene("이번에는 정상 팔 동작 중 사람 근접 보고를 넣습니다. 팔 움직임과 취소 결과를 확인하세요.")
        world.marker("AI 정상 팔 이동 명령")
        world.propose_arm(0.5)
        wait_for(lambda: world.primary_joint() > 0.25, 6, processes, "approved arm motion")
        arm_at = time.monotonic()
        if live:
            world.begin_person_walk(person_entry(*world.pose(), world.heading()),
                                    nearby_person(*world.pose(), world.heading()))
            world.marker("사람 접근 · 팔 중단 시험")
        else:
            world.human = nearby_person(*world.pose(), world.heading())
        # Existing arm policy stops on any human report, including a far
        # report. Do not wait for the person to cross the base's boundary.
        wait_for(lambda: any(t >= arm_at and value["arm_cancelling"]
                             for t, value in world.states), 3, processes, "arm cancellation request")
        world.marker("사람 감지, 팔 취소 요청")
        wait_for(lambda: world.states and not world.states[-1][1]["arm_cancelling"],
                 3, processes, "arm cancelled result")
        arm_cancel_position = world.primary_joint()
        arm_cancel_positions = world.arm_positions()
        time.sleep(0.35)
        if max(abs(a - b) for a, b in zip(world.arm_positions(), arm_cancel_positions)) > 0.02:
            raise AssertionError("arm kept moving after cancellation")
        sensor_person_evidence.append(check_person_sensor(world, "arm_stop", processes))
        world.marker("Gazebo 팔 관절 정지")
        if live:
            wait_for(world.person_walk_finished, 20, processes, "person approaches stopped arm")
            freeze_native_person()
        review_scene("팔 정지 장면", "사람 근접 보고 뒤 팔 동작이 취소됐습니다. 사람 표시는 다음 단계까지 유지됩니다.",
                     "외부 노드 공격 시험" if attack_probes and secure_graph else "연결 끊김 시험")
        clear_person()

        sensor_faults = {}
        for case in ("disconnect", "coverage"):
            sensor_faults[case] = exercise_sensor_fault(world, processes, case)
            presentation_wait(3)
        (root / "sensor-faults.json").write_text(json.dumps(sensor_faults, indent=2))
        (root / "sensor-person.json").write_text(json.dumps(sensor_person_evidence, indent=2))

        compound = exercise_compound(world, roles, processes, wait_for, compound_repeat, root / "compound-faults.json") if compound_repeat else None
        if compound:
            (root / "compound-faults.json").write_text(json.dumps(compound, indent=2))
            presentation_wait(3)

        snapshot = root / "sealed-snapshot.jsonl"
        wait_for(lambda: sealed_incident_snapshot(gate_directory / "sillok.jsonl", snapshot),
                 3, processes, "sealed arm incident")
        report = subprocess.run([binary, "sillok", "verify", "--log", str(snapshot),
                                 "--pubkey", roles.log_public if roles else public(9)], capture_output=True, text=True)
        if report.returncode or not json.loads(report.stdout)["fully_sealed"]:
            raise AssertionError("incident log is not fully sealed")

        attacks = {}
        if attack_probes and secure_graph:
            world.propose_base(0.0)
            wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                     5, processes, "base rearm before attacker",
                     action=lambda: world.propose_base(0.0))
            prepare_scene("공격자 노드가 바퀴에 직접 명령을 보내고 사람 정보를 위조합니다. 아래 공격 카드에서 결과를 확인하세요.")
            world.marker("외부 노드 바퀴 명령 공격")
            attack_at = time.monotonic()
            world_count_before = world.attack_world_received
            try:
                probe = probe_gazebo_permissions(role_env("proposal"))
                wait_for(lambda: any(t >= attack_at and value.get("decision")
                                     for t, value in world.outcomes),
                         2, processes, "authorized VLA proposal from attacker enclave")
                direct_received = sum(1 for t, value in world.commands
                                      if t >= attack_at and abs(value - 9.0) < 0.001)
                world_received = world.attack_world_received - world_count_before
                attacks["direct"] = {"blocked": direct_received == 0 and
                                     abs(world.speed()) < 0.03,
                                     "scope": "gazebo_sros2_graph",
                                     "unauthorized_received": direct_received,
                                     "attacker_uid": probe["uid"],
                                     "denied_at_publisher": probe["denied_at_publisher"]}
                attacks["world"] = {"blocked": world_received == 0,
                                    "scope": "gazebo_sros2_graph",
                                    "unauthorized_received": world_received}
            except Exception as exc:
                attacks["direct"] = {"blocked": False, "error": str(exc)}
                attacks["world"] = {"blocked": False, "error": str(exc)}
            for name in ("direct", "world"):
                world._emit("attack_result", attack=name, **attacks[name])
            review_scene("외부 노드 공격 결과", "바퀴 직접 명령과 사람 정보 위조 시험이 끝났습니다. 아래 두 카드의 결과를 확인하세요.",
                         "연결 끊김 시험")

        time.sleep(0.3)
        world.propose_base(0.0)
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                 5, processes, "base rearm before kill", action=lambda: world.propose_base(0.0))
        prepare_scene("로봇을 다시 움직인 뒤 해태 프로세스를 종료합니다. 명령이 끊겼을 때 바퀴가 멈추는지 확인하세요.")
        world.marker("두 번째 바퀴 이동")
        motion_at = time.monotonic()
        wait_for(lambda: world.speed() > 0.08 and (not live or time.monotonic() - motion_at >= 3),
                 8, processes, "second base motion",
                 action=lambda: world.propose_base(0.12 if live else 0.2))
        killed_at = time.monotonic()
        killed_sim_ms = world.get_clock().now().nanoseconds // 1_000_000
        world.marker("해태 프로세스 강제 종료")
        stop(processes.pop("gate"), force=True)
        stopped_at = wait_for(lambda: abs(world.speed()) < 0.03, 3, processes,
                              "controller deadman after gate kill")
        stopped_sim_ms = world.get_clock().now().nanoseconds // 1_000_000
        world.marker("컨트롤러 데드맨으로 바퀴 정지")
        if live:
            x, y = world.pose()
            world._emit("telemetry", x=x, y=y, speed=world.speed(),
                        joint=world.primary_joint(), joints=world.measured_joints(), yaw=world.heading(), humans=[])
        arm_fault_results = {}
        for case in ("kill", "stall", "delay"):
            # Independent test session, not an operator reset or automatic
            # production restart. Start only after the base and arm are held.
            if abs(world.speed()) >= 0.03:
                raise AssertionError("cannot start a new fault fixture while base moves")
            fault_root = root / ("arm-" + case)
            fault_root.mkdir()
            fixture(fault_root, binary, arm=True, arm_policy=arm_policy(),
                    person_distance=PERSON_DISTANCE_M)
            fault_params = json.loads((fault_root / "params.yaml").read_text())
            fault_params["haetae_gate"]["ros__parameters"].update({
                "use_sim_time": True, "heartbeat_topic": "/haetae_gate/heartbeat"})
            (fault_root / "params.yaml").write_text(json.dumps(fault_params))
            if roles:
                fault_gate = roles.configure_gate(fault_root)
                fault_params_path = fault_gate / "params.yaml"
            else:
                fault_gate = fault_root
                fault_params_path = fault_root / "params.yaml"
            world.states.clear()
            _, log = start([sys.executable, str(REPO / "ros/haetae_gate/node.py"),
                            "--ros-args", "--params-file", str(fault_params_path),
                            "-r", "/cmd_vel:=" + BASE_CONTROLLER_TOPIC],
                           fault_root, "gate", processes, role_env("gate"), UIDS["gate"] if roles else None)
            logs.append(log)
            wait_for(lambda: world.states and world.states[-1][1]["mode"] == "normal"
                 and world.states[-1][1].get("arm_controller_ready"),
                     10, processes, "new isolated arm fault fixture")
            wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"],
                     5, processes, "fault fixture rearm", action=lambda: world.propose_base(0.0))
            arm_fault_results[case] = exercise_arm_fault(world, processes, case)
            if roles and (fault_gate / "sillok.jsonl").exists():
                shutil.copy2(fault_gate / "sillok.jsonl", fault_root / "sillok.jsonl")
            (fault_root / "result.json").write_text(json.dumps(arm_fault_results[case], indent=2) + "\n")
            world._emit("arm_fault_result", **arm_fault_results[case])
            presentation_wait(2)
        if attack_probes:
            review_scene("연결 끊김 뒤 정지 장면", "바퀴 정지와 팔의 독립 정지를 확인했습니다. 팔 시험은 프로세스 종료, 엔진 멈춤, 지연 신호 세 가지입니다.",
                         "재전송·서명 변조 시험" if secure_graph else "외부 노드 공격 시험")
            if not secure_graph:
                world.marker("외부 노드 바퀴 명령 공격")
                try:
                    acl = probe_permissions(root / "permission-probe")
                    attacks["direct"] = {"blocked": acl["direct_command_blocked"],
                                         "scope": acl["scope"],
                                         "unauthorized_received": acl["unauthorized_received"]["direct_base"]}
                    attacks["world"] = {"blocked": acl["forged_world_blocked"],
                                        "scope": acl["scope"],
                                        "unauthorized_received": acl["unauthorized_received"]["forged_world"]}
                except Exception as exc:
                    attacks["direct"] = {"blocked": False, "error": str(exc)}
                    attacks["world"] = {"blocked": False, "error": str(exc)}
                for name in ("direct", "world"):
                    world._emit("attack_result", attack=name, **attacks[name])
                review_scene("외부 노드 공격 결과", "별도 ROS 그래프의 바퀴 명령과 사람 정보 위조 시험 결과를 확인하세요.",
                             "재전송·서명 변조 시험")
            prepare_scene("이미 승인된 명령을 다시 보내고, 서명된 사람 정보를 변조합니다. 별도 해태 엔진의 거부 결과를 확인하세요.")
            world.marker("서명된 명령 재전송 공격")
            try:
                signed = probe_signed_inputs(root / "signed-probe", binary)
                attacks["replay"] = {"blocked": signed["replay_blocked"],
                                     "scope": signed["scope"],
                                     "controller_command_mps": signed["replay_command_mps"],
                                     "reason": signed["replay_reason"]}
                attacks["signature"] = {"blocked": signed["forged_signature_blocked"],
                                        "scope": signed["scope"],
                                        "controller_command_mps": signed["forged_command_mps"],
                                        "reason": signed["forged_reason"]}
            except Exception as exc:
                attacks["replay"] = {"blocked": False, "error": str(exc)}
                attacks["signature"] = {"blocked": False, "error": str(exc)}
            for name in ("replay", "signature"):
                world._emit("attack_result", attack=name, **attacks[name])
            (root / "attack-result.json").write_text(json.dumps(attacks, indent=2) + "\n")
        result = {"ok": all(row["blocked"] for row in attacks.values()),
                  "source_revision": os.environ.get("HAETAE_REVISION", "unknown"),
                  "transport_isolation": transport_evidence,
                  "robot_model": MODEL_NAME,
                  "arm_joints": list(ARM_JOINTS),
                  "arm_faults": arm_fault_results,
                  "sensor_faults": sensor_faults,
                  "compound_faults": compound,
                  "native_person_measurements": sensor_person_evidence,
                  "arm_cancelled_positions_rad": arm_cancel_positions,
                  "controller": "Gazebo Harmonic gz_ros2_control",
                  "base_moved_m": round(moving_x - start_x, 3),
                  "human_to_zero_wall_ms": round((zero_at - revoked_at) * 1000, 1),
                  "human_to_zero_sim_ms": zero_sim_ms - revoked_sim_ms,
                  "person_entry_to_stop_report_sim_ms": revoked_sim_ms - entry_sim_ms,
                  "person_stop_report_distance_m": round(math.dist(trigger["robot"], trigger["human"]), 3),
                  "person_walk_clock": "gazebo_sim_time" if live else None,
                  "person_input": "gazebo_gpu_lidar",
                  "base_stop_distance_m": round(base_stop_x - moving_x, 3),
                  "arm_out_of_bounds_denied": True,
                  "arm_cancelled_at_rad": round(arm_cancel_position, 4),
                  "gate_kill_to_base_stop_wall_ms": round((stopped_at - killed_at) * 1000, 1),
                  "gate_kill_to_base_stop_sim_ms": stopped_sim_ms - killed_sim_ms,
                  "sillok_incident_snapshot_fully_sealed": True,
                  "attack_probes": attacks}
        if roles and (gate_directory / "sillok.jsonl").exists():
            shutil.copy2(gate_directory / "sillok.jsonl", root / "sillok.jsonl")
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        (root / "verification-report.json").write_text(json.dumps(public_report(result,
            os.environ.get("HAETAE_REVISION", "unknown"), live.session_id if live else root.name),
            ensure_ascii=False, indent=2))
        export_artifacts(root, output)
        if live:
            live.publish({"kind": "result", "sim_ms": stopped_sim_ms, "result": result})
            if live_hold_seconds:
                time.sleep(live_hold_seconds)
        print(json.dumps(result))
    finally:
        if roles and (gate_directory / "sillok.jsonl").exists():
            shutil.copy2(gate_directory / "sillok.jsonl", root / "sillok.jsonl")
        for process in list(processes.values())[::-1]:
            stop(process)
        if executor is not None:
            executor.shutdown()
        if world is not None:
            if world.native:
                world.native.close()
            world.destroy_node()
        rclpy.try_shutdown()
        if thread is not None:
            thread.join(timeout=2)
        for log in logs:
            log.close()
        if network_guard:
            network_guard.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", help="path to the built haetae executable")
    parser.add_argument("--out", type=Path, help="directory for logs and result.json")
    parser.add_argument("--live-port", type=int, help="serve live Gazebo/ROS telemetry")
    parser.add_argument("--live-bind", default="127.0.0.1",
                        help="HTTP bind address; keep the default outside a container")
    parser.add_argument("--wait-for-viewer", action="store_true",
                        help="start motion after a browser connects to the live page")
    parser.add_argument("--live-hold-seconds", type=float, default=5,
                        help="keep the completed live view connected for this many seconds")
    parser.add_argument("--gazebo-gui", action="store_true",
                        help="launch Gazebo's GUI on an existing display")
    parser.add_argument("--manual-start", action="store_true",
                        help="wait for a browser start button after the viewer connects")
    parser.add_argument("--step-through", action="store_true",
                        help="wait for the viewer between completed live scenes")
    parser.add_argument("--attack-probes", action="store_true",
                        help="run SROS2 and signed-input attack probes")
    parser.add_argument("--secure-graph", action="store_true",
                        help="run Gazebo and attacker with separate SROS2 enclaves")
    parser.add_argument("--arm-fault", choices=("kill", "stall", "delay"),
                        help="isolated moving-arm controller failure test")
    parser.add_argument("--compound-repeat", type=int, default=0,
                        help="repeat bounded compound sensor/delay/proposal-burst faults (1..30, secured graph)")
    args = parser.parse_args()
    if not 0 <= args.compound_repeat <= 30 or (args.compound_repeat and not args.secure_graph):
        parser.error("--compound-repeat requires --secure-graph and a value in 0..30")
    if args.wait_for_viewer and args.live_port is None:
        parser.error("--wait-for-viewer requires --live-port")
    if args.live_port is not None and not 1 <= args.live_port <= 65535:
        parser.error("--live-port must be between 1 and 65535")
    if args.live_hold_seconds < 0:
        parser.error("--live-hold-seconds must be nonnegative")
    if args.gazebo_gui and args.live_port is None:
        parser.error("--gazebo-gui requires --live-port")
    if args.manual_start and args.live_port is None:
        parser.error("--manual-start requires --live-port")
    if args.step_through and args.live_port is None:
        parser.error("--step-through requires --live-port")
    binary = str(Path(args.binary).resolve())
    live = LiveHub() if args.live_port is not None else None
    server = start_server(live, args.live_port, args.live_bind,
                          gazebo_gui=args.gazebo_gui,
                          manual_start=args.manual_start,
                          attack_probes=args.attack_probes,
                          secured_gazebo=args.secure_graph,
                          step_through=args.step_through) if live else None
    if server:
        print(f"Live view: http://127.0.0.1:{args.live_port}/", flush=True)
    shared = Path("/dev/shm")
    previous_term = signal.signal(signal.SIGTERM, interrupt_run)
    try:
        with tempfile.TemporaryDirectory(dir=shared if shared.is_dir() and os.access(shared, os.W_OK)
                                         else None) as directory:
            try:
                run(Path(directory), binary, live, args.wait_for_viewer,
                    args.live_hold_seconds if live else 0, args.gazebo_gui,
                    args.manual_start, args.attack_probes, args.secure_graph,
                    args.step_through, args.arm_fault, args.compound_repeat, args.out)
            except RunInterrupted:
                if not (Path(directory) / "result.json").exists():
                    (Path(directory) / "verification-report.json").write_text(json.dumps(public_report(
                        revision=os.environ.get("HAETAE_REVISION", "unknown"),
                        run_id=live.session_id if live else Path(directory).name, failed=True), ensure_ascii=False, indent=2))
                    (Path(directory) / "error.json").write_text(json.dumps({
                        "error_type": "RunInterrupted", "error": "operator requested shutdown"}) + "\n")
                raise
            except Exception as exc:
                (Path(directory) / "verification-report.json").write_text(json.dumps(public_report(
                    revision=os.environ.get("HAETAE_REVISION", "unknown"),
                    run_id=live.session_id if live else Path(directory).name, failed=True), ensure_ascii=False, indent=2))
                (Path(directory) / "error.json").write_text(json.dumps({
                    "error_type": type(exc).__name__, "error": str(exc),
                    "traceback": traceback.format_exc()}, indent=2) + "\n")
                if live:
                    live.fail({"kind": "error", "label": "시뮬레이션이 중단됐습니다",
                               "detail": "장면 완료 조건을 제시간에 확인하지 못했습니다."
                               if isinstance(exc, TimeoutError) else "시뮬레이터 실행 중 오류가 발생했습니다.",
                               "error_type": type(exc).__name__})
                raise
            finally:
                export_artifacts(directory, args.out)
    except RunInterrupted:
        raise SystemExit(143)
    except Exception:
        if live and args.live_hold_seconds:
            # Physics and ROS have already been stopped by run's finally.
            # Keep only the error page available, including after a refresh.
            traceback.print_exc()
            time.sleep(args.live_hold_seconds)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
