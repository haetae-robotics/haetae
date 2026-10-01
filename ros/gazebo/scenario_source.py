#!/usr/bin/env python3
"""Test scenario's proposal writer, running with only VLA DDS authority."""
import json
import sys
import threading
import time
import rclpy
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import TwistStamped
from rclpy.node import Node
from rclpy.parameter import Parameter
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint


def main():
    rclpy.init()
    node = Node("haetae_scenario_source", parameter_overrides=[
        Parameter("use_sim_time", Parameter.Type.BOOL, True)],
        automatically_declare_parameters_from_overrides=True)
    base = node.create_publisher(TwistStamped, "/vla/cmd_vel", 1)
    arm = node.create_publisher(JointTrajectory, "/vla/arm", 1)

    def receive():
        try:
            for line in sys.stdin:
                request = json.loads(line)
                if "burst" in request:
                    burst = request["burst"]
                    hz, seconds = burst["hz"], burst["seconds"]
                    if not 1 <= hz <= 100 or not 0 < seconds <= 2:
                        raise ValueError("test burst exceeds its bounded range")
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        msg = TwistStamped()
                        msg.header.stamp = node.get_clock().now().to_msg()
                        msg.twist.linear.x = 0.12
                        base.publish(msg)
                        time.sleep(1 / hz)
                elif "base" in request:
                    msg = TwistStamped()
                    msg.header.stamp = node.get_clock().now().to_msg()
                    msg.twist.linear.x = request["base"]
                    base.publish(msg)
                else:
                    msg = JointTrajectory()
                    msg.joint_names = request["joints"]
                    for millis, values in request["points"]:
                        point = JointTrajectoryPoint()
                        point.positions = values
                        point.time_from_start.sec, point.time_from_start.nanosec = divmod(millis, 1000)
                        point.time_from_start.nanosec *= 1_000_000
                        msg.points.append(point)
                    arm.publish(msg)
        finally:
            rclpy.try_shutdown()

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    try:
        rclpy.spin(node)
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
