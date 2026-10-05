#!/usr/bin/env python3
"""Untrusted transport principal: forwards permits, owns no Rust/key material."""
import rclpy
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory
from geometry_msgs.msg import TwistStamped
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


class Relay(Node):
    def __init__(self):
        super().__init__("haetae_gateway")
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.base = self.create_publisher(TwistStamped, "/diff_drive_base_controller/cmd_vel", 1)
        self.heartbeat = self.create_publisher(String, "/haetae_gate/heartbeat", qos)
        self.create_subscription(TwistStamped, "/haetae_authorized/cmd_vel", self.forward_base, 1)
        self.create_subscription(String, "/haetae_authorized/heartbeat", self.forward_heartbeat, qos)
        group = ReentrantCallbackGroup()
        self.client = ActionClient(self, FollowJointTrajectory,
                                   "/joint_trajectory_controller/follow_joint_trajectory", callback_group=group)
        self.handles = {}
        self.server = ActionServer(self, FollowJointTrajectory, "~/follow_joint_trajectory",
            self.execute, goal_callback=lambda request: GoalResponse.ACCEPT,
            cancel_callback=self.cancel, callback_group=group)

    def cancel(self, goal):
        handle = self.handles.get(bytes(goal.goal_id.uuid))
        if handle is not None:
            handle.cancel_goal_async()
        return CancelResponse.ACCEPT

    def forward_base(self, message):
        self.base.publish(message)

    def forward_heartbeat(self, message):
        self.heartbeat.publish(message)

    async def prepare_goal(self, request):
        return request

    async def execute(self, goal):
        identifier = bytes(goal.goal_id.uuid)
        try:
            if not self.client.server_is_ready():
                raise RuntimeError("controller unavailable")
            request = await self.prepare_goal(goal.request)
            handle = await self.client.send_goal_async(request,
                feedback_callback=lambda message: goal.publish_feedback(message.feedback))
            if not handle.accepted:
                raise RuntimeError("controller rejected permit")
            self.handles[identifier] = handle
            if goal.is_cancel_requested:
                await handle.cancel_goal_async()
            response = await handle.get_result_async()
            if response.status == GoalStatus.STATUS_SUCCEEDED:
                goal.succeed()
            elif response.status == GoalStatus.STATUS_CANCELED:
                goal.canceled()
            else:
                goal.abort()
            return response.result
        except Exception as exc:
            goal.abort()
            result = FollowJointTrajectory.Result()
            result.error_code = FollowJointTrajectory.Result.INVALID_GOAL
            result.error_string = str(exc)
            return result
        finally:
            self.handles.pop(identifier, None)


def main():
    rclpy.init()
    node = Relay()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
