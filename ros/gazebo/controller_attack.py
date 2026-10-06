#!/usr/bin/env python3
"""Relay-UID actuator injection. Receives public signed packets, never keys.

The root test driver separately observes trusted controller counters and actual
Gazebo joints/odometry. This process's acknowledgements are not verdicts.
"""
import json
import queue
import sys
import threading
import rclpy
from control_msgs.action import FollowJointTrajectory
from geometry_msgs.msg import TwistStamped
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectoryPoint


def main():
    rclpy.init(args=[])
    node = Node("haetae_compromised_gateway")
    qos = QoSProfile(depth=16, reliability=ReliabilityPolicy.BEST_EFFORT)
    base = node.create_publisher(TwistStamped, "/diff_drive_base_controller/cmd_vel", 16)
    heartbeat = node.create_publisher(String, "/haetae_gate/heartbeat", qos)
    arm = ActionClient(node, FollowJointTrajectory, "/joint_trajectory_controller/follow_joint_trajectory")
    packets = queue.Queue(maxsize=32)
    done = threading.Event()
    ready = False
    def reader():
        try:
            for line in sys.stdin:
                if len(line) > 16384:
                    raise ValueError("packet bound")
                packets.put(json.loads(line), timeout=1)
        finally:
            done.set()
    threading.Thread(target=reader, daemon=True).start()
    def deliver():
        nonlocal ready
        if not ready:
            if not (base.get_subscription_count() and heartbeat.get_subscription_count()
                    and arm.server_is_ready()):
                return
            ready = True
            print(json.dumps({"ready": True}), flush=True)
        try:
            packet = packets.get_nowait()
        except queue.Empty:
            return
        if "base" in packet:
            data = packet["base"]
            msg = TwistStamped()
            msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(data["stamp"], 1_000_000_000)
            msg.header.frame_id = data["permit"]
            msg.twist.linear.x, msg.twist.angular.z = data["linear"], data["angular"]
            base.publish(msg)
        if "heartbeat" in packet:
            heartbeat.publish(String(data=packet["heartbeat"]))
        if "arm" in packet:
            data = packet["arm"]
            goal = FollowJointTrajectory.Goal()
            goal.trajectory.joint_names = data["joints"]
            goal.trajectory.header.stamp.sec, goal.trajectory.header.stamp.nanosec = divmod(data["stamp"], 1_000_000_000)
            goal.trajectory.header.frame_id = data["permit"]
            for point in data["points"]:
                p = JointTrajectoryPoint()
                p.positions = point["positions"]
                p.time_from_start.sec, p.time_from_start.nanosec = divmod(point["time"], 1_000_000_000)
                goal.trajectory.points.append(p)
            future = arm.send_goal_async(goal)
            future.add_done_callback(lambda f: print(json.dumps({"goal_accepted": f.result().accepted}), flush=True))
        print(json.dumps({"delivered": packet.get("id")}), flush=True)
    node.create_timer(.002, deliver)
    try:
        while not done.is_set() or not packets.empty():
            rclpy.spin_once(node, timeout_sec=.02)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
