#!/usr/bin/env python3
"""Test scenario's proposal writer, running with only VLA DDS authority."""
import json
import sys
import threading
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
                if "base" in request:
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
