#!/usr/bin/env python3
"""Negative DDS writer checks plus a matched authorized writer per principal."""
import json
import sys
import time
import rclpy
from geometry_msgs.msg import TwistStamped
from trajectory_msgs.msg import JointTrajectory
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String, UInt64


def main():
    role = sys.argv[1]
    rclpy.init(args=[])
    node = Node("haetae_role_probe")
    if role == "gate":
        forbidden = ["/haetae_input/world", "/haetae_input/fault",
                     "/haetae_gate/signed/world", "/haetae_gate/signed/fault"]
        # An authorized diagnostic route proves DDS matching without adding
        # a second live actuator writer and correctly tripping the Hold latch.
        allowed = node.create_publisher(String, "/haetae_gate/state", 1)
    elif role in ("vla", "proposal"):
        forbidden = ["/haetae_input/world", "/haetae_input/fault", "/haetae_gate/signed/world",
                     "/haetae_gate/signed/fault", "/haetae_gate/heartbeat",
                     "/diff_drive_base_controller/cmd_vel", "/joint_trajectory_controller/joint_trajectory"]
        if role == "proposal":
            forbidden.append("/haetae_gate/signed/vla")
            allowed = node.create_publisher(TwistStamped, "/vla/cmd_vel", 1)
        else:
            forbidden.extend(["/vla/cmd_vel", "/vla/arm"])
            allowed = node.create_publisher(String, "/haetae_gate/signed/vla", 1)
    else:
        forbidden = ["/haetae_gate/heartbeat", "/diff_drive_base_controller/cmd_vel",
                     "/joint_trajectory_controller/joint_trajectory", "/haetae_gate/signed/vla"]
        allowed = node.create_publisher(String, "/haetae_input/world", 1)
    denied = {}
    for topic in forbidden:
        try:
            kind = (UInt64 if topic.endswith("heartbeat") else TwistStamped if topic.endswith("cmd_vel")
                    else JointTrajectory if topic.endswith("joint_trajectory") or topic == "/vla/arm" else String)
            node.create_publisher(kind, topic, 1)
            denied[topic] = False
        except Exception:
            denied[topic] = True
    if role != "gate":
        try:
            ActionClient(node, FollowJointTrajectory, "/joint_trajectory_controller/follow_joint_trajectory")
            denied["arm_action"] = False
        except Exception:
            denied["arm_action"] = True
    deadline = time.monotonic() + 8
    while allowed.get_subscription_count() == 0 and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
    result = {"role": role, "denied_writers": denied,
              "authorized_writer_matched": allowed.get_subscription_count() > 0}
    print(json.dumps(result))
    node.destroy_node()
    rclpy.try_shutdown()
    if not all(denied.values()) or not result["authorized_writer_matched"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
