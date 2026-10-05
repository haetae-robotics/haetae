#!/usr/bin/env python3
"""ROS 2 Jazzy transport for the Rust enforcement process.

The robot's base controller and joint trajectory controller must accept input
only from this node's SROS2 enclave, and independently stop when it dies.
"""

import json
import os
from pathlib import Path
import sys
import time

import rclpy
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory
from geometry_msgs.msg import Twist, TwistStamped
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String, UInt64
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from bridge import (Bridge, BridgeFailure, StaleActuation, ExpiredActuation,
                    require_fresh_actuation, lease_renewable, reject_expired_actuation)
from signing import Signer
from proposals import InvalidProposal, base_action, arm_action
from controller_permits import PermitSigner, IDLE, base_digest, arm_digest, explicit_rearm


class HaetaeGate(Node):
    def __init__(self):
        super().__init__("haetae_gate")
        defaults = {
            "haetae_bin": "haetae", "policy_path": "", "state_path": "",
            "sillok_path": "", "key_path": "", "trust_path": "", "root_pubkey": "",
            "keys_json": "{}", "inputs_json": "[]", "arm_inputs_json": "[]",
            "arm_action": "/joint_trajectory_controller/follow_joint_trajectory",
            "response_timeout_ms": 500, "max_actuation_response_ms": 50,
            "tick_hz": 20.0, "output_stamped": True,
            "heartbeat_topic": "", "signed_inputs_only": False,
            "controller_key_path": "",
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        param = lambda key: self.get_parameter(key).value
        required = ("policy_path", "state_path", "sillok_path", "key_path", "trust_path", "root_pubkey")
        if any(not param(key) for key in required):
            raise ValueError("all policy, state, log, signing and trust paths are required")
        self.policy = json.loads(open(param("policy_path"), encoding="utf-8").read())
        self.world_max_age_ms = int(self.policy["freshness"]["world_max_age_ms"])
        self.max_actuation_response_ms = int(param("max_actuation_response_ms"))
        if self.max_actuation_response_ms <= 0:
            raise ValueError("max_actuation_response_ms must be positive")
        self.arm_joints = [j["name"] for j in self.policy.get("arm", {}).get("joints", [])]
        self.signed_inputs_only = param("signed_inputs_only")
        if self.signed_inputs_only and any(json.loads(param(key)) != value for key, value in (
                ("keys_json", {}), ("inputs_json", []), ("arm_inputs_json", []))):
            raise ValueError("signed-only gateway must have no source signing keys or raw inputs")
        self.signer = None if self.signed_inputs_only else Signer(param("trust_path"), param("state_path"), json.loads(param("keys_json")))
        argv = [param("haetae_bin"), "enforce", "--stdio", "--policy", param("policy_path"),
                "--state", param("state_path"), "--sillok", param("sillok_path"),
                "--key", param("key_path"), "--trust", param("trust_path"),
                "--root-pubkey", param("root_pubkey")]
        self.bridge = Bridge(argv, int(param("response_timeout_ms")))
        self.output_stamped = param("output_stamped")
        self.permits = PermitSigner(Path(param("controller_key_path"))) if param("controller_key_path") else None
        self.permit_reset = False
        self.permit_stop = True
        self.permit_remaining_ns = 200_000_000
        self.permit_sim_ns = 0
        self.permit_wall_ns = 0
        if self.permits:
            if not self.output_stamped:
                raise ValueError("controller permits require stamped output")
            for target, topic in (("base", "/diff_drive_base_controller/guard_state"),
                                  ("arm", "/joint_trajectory_controller/guard_state")):
                self.create_subscription(String, topic,
                    lambda msg, target=target: self.permits.observe(target, json.loads(msg.data)),
                    QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                               durability=DurabilityPolicy.TRANSIENT_LOCAL))
        if not isinstance(self.output_stamped, bool):
            raise ValueError("output_stamped must be a bool")
        self.command_pub = self.create_publisher(
            TwistStamped if self.output_stamped else Twist, "/cmd_vel", 1)
        self.heartbeat_pub = (self.create_publisher(String if self.permits else UInt64, param("heartbeat_topic"),
                             QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
                              if param("heartbeat_topic") else None)
        self.state_pub = self.create_publisher(String, "~/state", QoSProfile(
            depth=1, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.outcome_pub = self.create_publisher(String, "~/outcome", 16)
        self.decision_pub = self.create_publisher(String, "~/decision", 16)
        self.arm_client = ActionClient(self, FollowJointTrajectory, param("arm_action"))
        self.arm_goal = None
        self.arm_goal_future = None
        self.arm_goal_deadline = None
        self.arm_result_future = None
        self.arm_cancel_future = None
        self.arm_cancel_deadline = None
        self.cancel_requested = False
        self.failed = False
        self.abort_deadline = None
        self.seq = {role: 0 for role in ("vla", "planner", "teleop", "peer")}
        reliable = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
        best_effort = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        if self.signed_inputs_only:
            for role in ("world", "fault", "vla"):
                self.create_subscription(String, "~/signed/" + role,
                    lambda msg, role=role: self._signed(msg, role),
                    best_effort if role == "world" else reliable)
        else:
            self.create_subscription(String, "~/world", self._world, best_effort)
            self.create_subscription(String, "~/fault", self._fault, reliable)
        self._input_subscriptions = []
        for item in json.loads(param("inputs_json")):
            topic, role, ttl = item["topic"], item["source"], int(item["ttl_ms"])
            if role not in self.seq or role not in self.signer.keys or ttl <= 0:
                raise ValueError("invalid base input binding")
            self._input_subscriptions.append(self.create_subscription(
                TwistStamped, topic, lambda msg, role=role, ttl=ttl: self._twist(msg, role, ttl), reliable))
        for item in json.loads(param("arm_inputs_json")):
            topic, role = item["topic"], item["source"]
            if role not in self.seq or role not in self.signer.keys or not self.arm_joints:
                raise ValueError("invalid arm input binding")
            self._input_subscriptions.append(self.create_subscription(
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
        if self.permits:
            self.permit_reset = explicit_rearm(step)
            status = step.get("status") or {}
            self.permit_stop = not status.get("armed") or status.get("mode") not in ("normal", "caution")
        self._publish_command(float(step["cmd"]["linear"]), float(step["cmd"]["angular"]))
        if step.get("arm"):
            arm = step["arm"]
            if isinstance(arm, dict) and "execute" in arm:
                self._execute_arm(arm["execute"]["points"])
            elif arm == "cancel":
                self._cancel_arm()
            else:
                raise BridgeFailure("invalid arm output")
        if step.get("status") is not None:
            self.state_pub.publish(String(data=json.dumps({**step["status"],
                "arm_controller_ready": ((not self.permits or len(self.permits.challenges) == 2)
                    and self.arm_client.server_is_ready()) if self.arm_joints else True})))
        if step.get("outcome") is not None:
            self.outcome_pub.publish(String(data=json.dumps(step["outcome"])))
            if "decision" in step["outcome"]:
                self.decision_pub.publish(String(data=json.dumps(step["outcome"]["decision"])))

    def _execute_arm(self, points):
        if self.arm_goal_future is not None or self.arm_goal is not None:
            raise BridgeFailure("previous arm goal has not completed")
        if not self.arm_joints or not self.arm_client.wait_for_server(timeout_sec=0.0):
            raise BridgeFailure("arm controller unavailable")
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = self.arm_joints
        goal.trajectory.header.stamp = self.get_clock().now().to_msg()
        if self.permits:
            goal.trajectory.header.stamp.sec, goal.trajectory.header.stamp.nanosec = divmod(
                self.permit_sim_ns, 1_000_000_000)
        for point in points:
            p = JointTrajectoryPoint()
            p.positions = [float(v) for v in point["positions"]]
            millis = int(point["time_from_start_ms"])
            p.time_from_start.sec, p.time_from_start.nanosec = divmod(millis, 1000)
            p.time_from_start.nanosec *= 1_000_000
            goal.trajectory.points.append(p)
        if self.permits:
            self.permits.active_arm = arm_digest(goal.trajectory)
            goal.trajectory.header.frame_id = self._permit("arm", "goal", self.permits.active_arm)
        self.cancel_requested = False
        self.arm_goal_deadline = time.monotonic() + 0.25
        self.arm_goal_future = self.arm_client.send_goal_async(goal)
        self.arm_goal_future.add_done_callback(self._on_arm_goal)

    def _on_arm_goal(self, future):
        try:
            self.arm_goal = future.result()
            self.arm_goal_future = None
            self.arm_goal_deadline = None
            if self.failed:
                if self.arm_goal.accepted:
                    self.arm_goal.cancel_goal_async()
                return
            if not self.arm_goal.accepted:
                raise BridgeFailure("arm goal rejected")
            self.arm_result_future = self.arm_goal.get_result_async()
            self.arm_result_future.add_done_callback(self._on_arm_result)
            if self.cancel_requested:
                self._cancel_arm()
        except Exception as exc:
            self._abort(exc)

    def _on_arm_result(self, future):
        try:
            status = future.result().status
            if self.cancel_requested and status != GoalStatus.STATUS_CANCELED and not (
                    self.permits and status == GoalStatus.STATUS_ABORTED):
                raise BridgeFailure("arm did not report a cancelled result")
            if not self.cancel_requested and status != GoalStatus.STATUS_SUCCEEDED:
                raise BridgeFailure("arm goal did not succeed")
            self.arm_goal = None
            self.arm_result_future = None
            self.arm_cancel_deadline = None
            self.cancel_requested = False
            if self.permits:
                self.permits.active_arm = IDLE
        except Exception as exc:
            self._abort(exc)

    def _on_arm_cancel(self, future):
        try:
            response = future.result()
            self.arm_cancel_future = None
            if not response.goals_canceling and self.arm_goal is not None and not self.permits:
                raise BridgeFailure("arm controller rejected cancellation")
        except Exception as exc:
            self._abort(exc)

    def _cancel_arm(self):
        if self.permits and "arm" in self.permits.challenges:
            self.heartbeat_pub.publish(String(data=self._permit("arm", "stop", IDLE)))
            self.permits.active_arm = IDLE
        if self.arm_goal is None and self.arm_goal_future is None:
            return
        self.cancel_requested = True
        if self.arm_cancel_deadline is None:
            self.arm_cancel_deadline = time.monotonic() + 0.25
        if self.arm_goal is not None and self.arm_cancel_future is None:
            self.arm_cancel_future = self.arm_goal.cancel_goal_async()
            self.arm_cancel_future.add_done_callback(self._on_arm_cancel)

    def _abort(self, exc):
        if self.failed:
            return
        self.failed = True
        self.get_logger().error("Haetae bridge failed: " + str(exc))
        self._publish_zero()
        try:
            self._cancel_arm()
        except Exception as cancel_exc:
            self.get_logger().error("Arm cancel request failed: " + str(cancel_exc))
        if isinstance(exc, StaleActuation):
            try:
                self.bridge.request({"k": "reject", "t": self._now(), "reason": str(exc)})
                # A response is flushed before its zero-command log commit.
                # The next response proves the reject was sealed before kill.
                self.bridge.request({"k": "tick", "t": self._now()})
            except Exception as record_exc:
                self.get_logger().error("Stale actuation incident could not be sealed: "
                                        + str(record_exc))
        try:
            self.bridge.close()
        except Exception as close_exc:
            self.get_logger().error("Bridge close failed: " + str(close_exc))
        self.abort_deadline = time.monotonic() + 0.25
        self.create_timer(0.02, self._abort_tick)

    def _publish_command(self, linear, angular):
        if self.output_stamped:
            command = TwistStamped()
            command.header.stamp = self.get_clock().now().to_msg()
            twist = command.twist
        else:
            command = Twist()
            twist = command
        twist.linear.x = linear
        twist.angular.z = angular
        if self.permits:
            if "base" not in self.permits.challenges:
                if linear or angular:
                    raise BridgeFailure("base challenge unavailable")
                return
            command.header.stamp.sec, command.header.stamp.nanosec = divmod(self.permit_sim_ns, 1_000_000_000)
            command.header.frame_id = self._permit("base", "reset" if self.permit_reset else
                "stop" if linear == 0 and angular == 0 and self.permit_stop else "command", base_digest(command))
        self.command_pub.publish(command)

    def _permit(self, target, kind, digest):
        return self.permits.sign(target, kind, digest, self.permit_sim_ns,
                                 self.permit_remaining_ns, self.permit_wall_ns)

    def _heartbeat(self, started_ros_ms):
        if self.permits:
            if "arm" not in self.permits.challenges:
                return
            self.heartbeat_pub.publish(String(data=self._permit("arm",
                "reset" if self.permit_reset else "lease", IDLE if self.permit_reset else self.permits.active_arm)))
        else:
            self.heartbeat_pub.publish(UInt64(data=started_ros_ms))

    def _publish_zero(self):
        if self.permits:
            self.permit_reset = False
            self.permit_stop = True
        self._publish_command(0.0, 0.0)

    def _abort_tick(self):
        self._publish_zero()
        if time.monotonic() >= self.abort_deadline:
            os._exit(2)

    def _receive(self, callback):
        if self.failed:
            return
        try:
            started = time.monotonic()
            started_ros_ms = self._now()
            if self.permits:
                self.permit_sim_ns = started_ros_ms * 1_000_000
                self.permit_wall_ns = time.monotonic_ns()
            step = callback()
            now_ros_ms = self._now()
            elapsed_ms = (-1 if now_ros_ms < started_ros_ms else max(
                (time.monotonic() - started) * 1000, now_ros_ms - started_ros_ms))
            require_fresh_actuation(
                step, elapsed_ms, now_ros_ms,
                self.world_max_age_ms, self.max_actuation_response_ms)
            if self.permits:
                status = step.get("status") or {}
                remaining_ms = min(200, self.world_max_age_ms - (status.get("world_age_ms") or 0))
                if status.get("active_expires_ms") is not None:
                    remaining_ms = min(remaining_ms, status["active_expires_ms"] - started_ros_ms)
                self.permit_remaining_ns = max(1, remaining_ms) * 1_000_000
            self._publish(step)
            if (self.heartbeat_pub and not self.cancel_requested and not self.failed
                    and (not self.permits or self.arm_goal_future is None)
                    and lease_renewable(step, elapsed_ms,
                    self.world_max_age_ms, self.max_actuation_response_ms)):
                # Use the request's clock, never restamp a delayed response.
                self._heartbeat(started_ros_ms)
        except ExpiredActuation as exc:
            # Expiry at the response boundary is a normal loss of authority.
            # Stop before any further IPC; reject clears engine goals/arming.
            # Keep watchdogs alive, but recovery needs a new explicit stop.
            self._publish_zero()
            try:
                self._cancel_arm()
                self._publish(reject_expired_actuation(self.bridge, exc, self._now()))
            except Exception as bridge_exc:
                self._abort(bridge_exc)
        except InvalidProposal as exc:
            try:
                self.get_logger().warning("Rejected malformed proposal: " + str(exc))
                self._publish(self.bridge.request({"k": "reject", "t": self._now(),
                                                   "reason": str(exc)}))
            except Exception as bridge_exc:
                self._abort(bridge_exc)
        except Exception as exc:
            self._abort(exc)

    def _world(self, msg):
        self._receive(lambda: self._send("world", msg.data))

    def _fault(self, msg):
        self._receive(lambda: self._send("fault", msg.data))

    def _signed(self, msg, role):
        def send():
            try:
                envelope = json.loads(msg.data)
                if not isinstance(envelope, dict) or envelope.get("role") != role:
                    raise InvalidProposal("signed input role does not match topic binding")
            except (ValueError, TypeError) as exc:
                raise InvalidProposal("malformed signed input") from exc
            return self.bridge.request({"k": "signed", "t": self._now(), "data": msg.data})
        self._receive(send)

    def _proposal(self, role, action):
        self.seq[role] += 1
        return self._send(role, {"id": self.seq[role], "source": role,
                               "timestamp_ms": self._now(), "action": action})

    def _twist(self, msg, role, ttl):
        self._receive(lambda: self._proposal(role, base_action(msg, ttl)))

    def _arm(self, msg, role):
        self._receive(lambda: self._proposal(role, arm_action(msg, self.arm_joints)))

    def _tick(self):
        def request():
            if self.arm_goal_deadline is not None and time.monotonic() >= self.arm_goal_deadline:
                raise BridgeFailure("arm goal acceptance did not complete in 250 ms")
            if self.arm_cancel_deadline is not None and time.monotonic() >= self.arm_cancel_deadline:
                raise BridgeFailure("arm cancellation did not complete in 250 ms")
            if self.count_publishers(self.command_pub.topic_name) > 1:
                if self.signer:
                    return self._send("fault", {"code": "rogue-cmd-vel-publisher",
                        "timestamp_ms": self._now(), "raise_to": "hold"})
                return self.bridge.request({"k": "hold", "t": self._now()})
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
