#!/usr/bin/env python3
"""Bounded trusted test reset without ROS CLI discovery/daemon state."""
import sys
import rclpy
from controller_manager_msgs.srv import SwitchController
from rclpy.node import Node


def main():
    name, state = sys.argv[1:]
    if name not in ("diff_drive_base_controller", "joint_trajectory_controller") or state not in ("inactive", "active"):
        raise ValueError("unsupported test controller reset")
    rclpy.init(args=[])
    node = Node("haetae_controller_test_reset")
    try:
        client = node.create_client(SwitchController, "/controller_manager/switch_controller")
        if not client.wait_for_service(timeout_sec=5):
            raise RuntimeError("controller switch service unavailable")
        request = SwitchController.Request()
        if state == "inactive":
            request.deactivate_controllers = [name]
        else:
            request.activate_controllers = [name]
        request.strictness = 2
        request.activate_asap = True
        request.timeout.sec = 2
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=5)
        if not future.done() or future.result() is None or not future.result().ok:
            raise RuntimeError("controller switch failed")
        print(name + " " + state)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
