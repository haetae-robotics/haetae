#!/usr/bin/env python3
"""ROS 2 Jazzy transport for the Rust enforcement process.

The robot's base controller and joint trajectory controller must accept input
only from this node's SROS2 enclave, and independently stop when it dies.
"""

import json
import os
import sys

import rclpy
from control_msgs.action import FollowJointTrajectory
from geometry_msgs.msg import TwistStamped
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from bridge import Bridge, BridgeFailure
from signing import Signer


class HaetaeGate(Node):
    def __init__(self):
        super().__init__("haetae_gate")
        defaults = {
            "haetae_bin": "haetae", "policy_path": "", "state_path": "",
            "sillok_path": "", "key_path": "", "trust_path": "", "root_pubkey": "",
            "keys_json": "{}", "inputs_json": "[]", "arm_inputs_json": "[]",
            "arm_action": "/joint_trajectory_controller/follow_joint_trajectory",
            "response_timeout_ms": 20, "tick_hz": 20.0,
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        param = lambda key: self.get_parameter(key).value
        required = ("policy_path", "state_path", "sillok_path", "key_path", "trust_path", "root_pubkey")
        if any(not param(key) for key in required):
            raise ValueError("all policy, state, log, signing and trust paths are required")
        self.policy = json.loads(open(param("policy_path"), encoding="utf-8").read())
        self.arm_joints = [j["name"] for j in self.policy.get("arm", {}).get("joints", [])]
        self.signer = Signer(param("trust_path"), param("state_path"), json.loads(param("keys_json")))
        argv = [param("haetae_bin"), "enforce", "--stdio", "--policy", param("policy_path"),
                "--state", param("state_path"), "--sillok", param("sillok_path"),
                "--key", param("key_path"), "--trust", param("trust_path"),
                "--root-pubkey", param("root_pubkey")]
        self.bridge = Bridge(argv, int(param("response_timeout_ms")))
        self.command_pub = self.create_publisher(TwistStamped, "/cmd_vel", 1)
        self.state_pub = self.create_publisher(String, "~/state", QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.outcome_pub = self.create_publisher(String, "~/outcome", 16)
        self.decision_pub = self.create_publisher(String, "~/decision", 16)
        self.arm_client = ActionClient(self, FollowJointTrajectory, param("arm_action"))
        self.arm_goal = None
        self.arm_goal_future = None
        self.cancel_requested = False
        self.seq = {role: 0 for role in ("vla", "planner", "teleop", "peer")}
        reliable = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
        best_effort = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(String, "~/world", self._world, best_effort)
        self.create_subscription(String, "~/fault", self._fault, reliable)
        self.subscriptions = []
        for item in json.loads(param("inputs_json")):
            topic, role, ttl = item["topic"], item["source"], int(item["ttl_ms"])
            if role not in self.seq or role not in self.signer.keys or ttl <= 0:
                raise ValueError("invalid base input binding")
            self.subscriptions.append(self.create_subscription(
                TwistStamped, topic, lambda msg, role=role, ttl=ttl: self._twist(msg, role, ttl), reliable))
        for item in json.loads(param("arm_inputs_json")):
            topic, role = item["topic"], item["source"]
            if role not in self.seq or role not in self.signer.keys or not self.arm_joints:
                raise ValueError("invalid arm input binding")
            self.subscriptions.append(self.create_subscription(
                JointTrajectory, topic, lambda msg, role=role: self._arm(msg, role), reliable))
        hz = float(param("tick_hz"))
        if hz <= 0 or hz > 100:
            raise ValueError("tick_hz must be in (0,100]")
        self.create_timer(1.0 / hz, self._tick)

    def _now(self):
        return self.get_clock().now().nanoseconds // 1_000_000

    def _send(self, role, payload):
        signed = self.signer.sign(role, payload)
        return self.bridge.request({"k": "signed", "t": self._now(),
                                    "data": json.dumps(signed, separators=(",", ":"))})

    def _publish(self, step):
        cmd = TwistStamped()
        cmd.header.stamp = self.get_clock().now().to_msg()
        cmd.twist.linear.x = float(step["cmd"]["linear"])
        cmd.twist.angular.z = float(step["cmd"]["angular"])
        self.command_pub.publish(cmd)
        if step.get("arm"):
            arm = step["arm"]
            if isinstance(arm, dict) and "execute" in arm:
                self._execute_arm(arm["execute"]["points"])
            elif arm == "cancel":
                self._cancel_arm()
            else:
                raise BridgeFailure("invalid arm output")
        if step.get("status") is not None:
            self.state_pub.publish(String(data=json.dumps(step["status"])))
        if step.get("outcome") is not None:
            self.outcome_pub.publish(String(data=json.dumps(step["outcome"])))
            if "decision" in step["outcome"]:
                self.decision_pub.publish(String(data=json.dumps(step["outcome"]["decision"])))

    def _execute_arm(self, points):
        if not self.arm_joints or not self.arm_client.wait_for_server(timeout_sec=0.0):
            raise BridgeFailure("arm controller unavailable")
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = self.arm_joints
        goal.trajectory.header.stamp = self.get_clock().now().to_msg()
        for point in points:
            p = JointTrajectoryPoint()
            p.positions = [float(v) for v in point["positions"]]
            millis = int(point["time_from_start_ms"])
            p.time_from_start.sec, p.time_from_start.nanosec = divmod(millis, 1000)
            p.time_from_start.nanosec *= 1_000_000
            goal.trajectory.points.append(p)
        self.cancel_requested = False
        self.arm_goal_future = self.arm_client.send_goal_async(goal)
        self.arm_goal_future.add_done_callback(self._on_arm_goal)

    def _on_arm_goal(self, future):
        try:
            self.arm_goal = future.result()
            if not self.arm_goal.accepted:
                raise BridgeFailure("arm goal rejected")
            if self.cancel_requested:
                self.arm_goal.cancel_goal_async()
        except Exception as exc:
            self._abort(exc)

    def _cancel_arm(self):
        self.cancel_requested = True
        if self.arm_goal is not None:
            self.arm_goal.cancel_goal_async()

    def _abort(self, exc):
        self.get_logger().error("Haetae bridge failed: " + str(exc))
        zero = TwistStamped()
        zero.header.stamp = self.get_clock().now().to_msg()
        self.command_pub.publish(zero)
        self._cancel_arm()
        self.bridge.close()
        os._exit(2)

    def _receive(self, callback):
        try:
            self._publish(callback())
        except Exception as exc:
            self._abort(exc)

    def _world(self, msg):
        self._receive(lambda: self._send("world", msg.data))

    def _fault(self, msg):
        self._receive(lambda: self._send("fault", msg.data))

    def _twist(self, msg, role, ttl):
        def send():
            if any((msg.twist.linear.y, msg.twist.linear.z, msg.twist.angular.x, msg.twist.angular.y)):
                raise ValueError("unsupported TwistStamped component")
            self.seq[role] += 1
            linear, angular = msg.twist.linear.x, msg.twist.angular.z
            action = {"type": "stop"} if linear == 0.0 and angular == 0.0 else {
                "type": "velocity", "linear": linear, "angular": angular, "ttl_ms": ttl}
            payload = {"id": self.seq[role], "source": role, "timestamp_ms": self._now(), "action": action}
            return self._send(role, payload)
        self._receive(send)

    def _arm(self, msg, role):
        def send():
            if list(msg.joint_names) != self.arm_joints:
                raise ValueError("wrong arm joint names")
            if not msg.points:
                self.seq[role] += 1
                payload = {"id": self.seq[role], "source": role,
                           "timestamp_ms": self._now(), "action": {"type": "stop"}}
                return self._send(role, payload)
            points = []
            for p in msg.points:
                if p.velocities or p.accelerations or p.effort:
                    raise ValueError("only positions are accepted")
                millis = p.time_from_start.sec * 1000 + p.time_from_start.nanosec // 1_000_000
                points.append({"time_from_start_ms": millis, "positions": list(p.positions)})
            self.seq[role] += 1
            payload = {"id": self.seq[role], "source": role, "timestamp_ms": self._now(),
                       "action": {"type": "joint_trajectory", "points": points,
                                  "ttl_ms": points[-1]["time_from_start_ms"]}}
            return self._send(role, payload)
        self._receive(send)

    def _tick(self):
        def request():
            if self.count_publishers("/cmd_vel") > 1:
                return self._send("fault", {"code": "rogue-cmd-vel-publisher", "timestamp_ms": self._now(), "raise_to": "hold"})
            return self.bridge.request({"k": "tick", "t": self._now()})
        self._receive(request)


def main():
    rclpy.init()
    node = HaetaeGate()
    try:
        rclpy.spin(node)
    finally:
        node.bridge.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
