"""Attempt protected ROS writes with only the VLA enclave on the Gazebo graph."""

import json
import os
import time

import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.node import Node
from std_msgs.msg import String


def main():
    rclpy.init(args=["--ros-args", "-e", "/haetae/vla"])
    node = Node("vla_source")
    denied = []

    def publisher(message_type, topic):
        try:
            return node.create_publisher(message_type, topic, 10)
        except Exception as exc:
            denied.append({"topic": topic, "error": type(exc).__name__})
            return None

    proposal = publisher(TwistStamped, "/vla/cmd_vel")
    direct = publisher(TwistStamped, "/diff_drive_base_controller/cmd_vel")
    forged_world = publisher(String, "/haetae_input/world")
    if proposal is None:
        raise RuntimeError("allowed VLA proposal publisher could not be created")

    discovery_deadline = time.monotonic() + 8
    while proposal.get_subscription_count() == 0 and time.monotonic() < discovery_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    matched = proposal.get_subscription_count() > 0
    started = time.monotonic()
    while time.monotonic() - started < 2.0:
        zero = TwistStamped()
        proposal.publish(zero)
        if direct:
            attack = TwistStamped()
            attack.twist.linear.x = 9.0
            direct.publish(attack)
        if forged_world:
            payload = {"stamp_ms": node.get_clock().now().nanoseconds // 1_000_000,
                       "robot": {"pose": {"x": 5.0, "y": 5.0}, "yaw": 0.0},
                       "humans": [], "confidence": 0.314159}
            forged_world.publish(String(data=json.dumps(payload)))
        rclpy.spin_once(node, timeout_sec=0.05)
    print(json.dumps({"attempted": {"direct_base": True, "forged_world": True},
                      "denied_at_publisher": denied, "allowed_proposal_created": True,
                      "allowed_proposal_matched": matched, "uid": os.getuid()}))
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
