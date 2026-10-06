#!/usr/bin/env python3
"""Bounded trusted test reset without ROS CLI discovery/daemon state."""
import json
import sys
import rclpy
from controller_manager_msgs.srv import SwitchController
from rclpy.node import Node


CONTROLLERS = ("diff_drive_base_controller", "joint_trajectory_controller")


def switch(node, client, names, state):
    request = SwitchController.Request()
    if state == "inactive":
        request.deactivate_controllers = list(names)
    else:
        request.activate_controllers = list(names)
    request.strictness = 2
    request.activate_asap = True
    request.timeout.sec = 2
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=5)
    if not future.done() or future.result() is None or not future.result().ok:
        raise RuntimeError("controller switch failed")


def main():
    persistent = sys.argv[1:] == ["--stdio"]
    if not persistent:
        name, state = sys.argv[1:]
        if name not in CONTROLLERS or state not in ("inactive", "active"):
            raise ValueError("unsupported test controller reset")
    rclpy.init(args=[])
    node = Node("haetae_controller_test_reset")
    try:
        client = node.create_client(SwitchController, "/controller_manager/switch_controller")
        if not client.wait_for_service(timeout_sec=5):
            raise RuntimeError("controller switch service unavailable")
        if persistent:
            # Keep one DDS participant for this trusted maintenance fixture.
            # Repeated short-lived CLI nodes accumulate discovery churn.
            for line in sys.stdin:
                if len(line) > 64:
                    raise ValueError("reset request bound")
                identifier = json.loads(line)
                if type(identifier) is not int or not 1 <= identifier <= 100:
                    raise ValueError("invalid reset identifier")
                for state in ("inactive", "active"):
                    switch(node, client, CONTROLLERS, state)
                print(json.dumps({"reset_complete": identifier}), flush=True)
        else:
            switch(node, client, [name], state)
            print(name + " " + state)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
