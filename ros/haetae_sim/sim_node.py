"""ROS-facing 100 Hz reference base controller and 20 Hz world publisher."""

import json
import time

from geometry_msgs.msg import TwistStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from std_msgs.msg import String

from kinematics import Base
from oracle import disc_gap, disc_hits_rect


class SimNode(Node):
    def __init__(self, start_x=5.0):
        super().__init__("haetae_sim")
        self.base = Base(x=start_x)
        self.human = None
        self.drop_world = False
        self.world_delay_ms = 0
        self.trace = []
        self.commands = []
        self.world_events = []
        self.states = []
        self.outcomes = []
        self.last_step = time.monotonic()
        self.world_pub = self.create_publisher(String, "/haetae_gate/world",
                                               QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.clock_pub = self.create_publisher(Clock, "/clock", 1)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 1)
        self.vla_pub = self.create_publisher(TwistStamped, "/vla/cmd_vel", 1)
        self.fault_pub = self.create_publisher(String, "/haetae_gate/fault", 1)
        self.create_subscription(TwistStamped, "/cmd_vel", self._command, 1)
        self.create_subscription(String, "/haetae_gate/state",
                                 lambda m: self.states.append((time.monotonic(), json.loads(m.data))), 10)
        self.create_subscription(String, "/haetae_gate/outcome",
                                 lambda m: self.outcomes.append((time.monotonic(), json.loads(m.data))), 10)
        self.create_timer(0.01, self._step)
        self.create_timer(0.05, self._world)

    def _command(self, msg):
        now = time.monotonic()
        linear = msg.twist.linear.x
        self.base.command(linear, msg.twist.angular.z, now)
        self.commands.append((now, linear))

    def _step(self):
        now = time.monotonic()
        remaining = now - self.last_step
        while remaining > 0:
            dt = min(remaining, 0.01)
            self.base.advance(dt, now - remaining + dt)
            remaining -= dt
        self.last_step = now
        gap = None if self.human is None else disc_gap(
            self.base.x, self.base.y, 0.25, self.human[0], self.human[1], 0.2)
        self.trace.append({"time_s": now, "x": self.base.x, "y": self.base.y,
                           "speed": self.base.speed, "target": self.base.target,
                           "deadman": self.base.last_command is None or
                           now - self.base.last_command >= self.base.deadman_s,
                           "gap": gap, "zone_entry": disc_hits_rect(
                               self.base.x, self.base.y, 0.25, 7.0, 4.0, 8.0, 6.0)})

    def _world(self):
        now = time.monotonic()
        stamp = self.get_clock().now()
        clock = Clock()
        clock.clock = stamp.to_msg()
        self.clock_pub.publish(clock)
        odom = Odometry()
        odom.header.stamp = stamp.to_msg()
        odom.header.frame_id = "map"
        odom.pose.pose.position.x = self.base.x
        odom.pose.pose.position.y = self.base.y
        odom.twist.twist.linear.x = self.base.speed
        self.odom_pub.publish(odom)
        if self.drop_world:
            return
        people = [] if self.human is None else [{"id": "child", "class": "child",
                                                 "pos": {"x": self.human[0], "y": self.human[1]}}]
        payload = {"stamp_ms": stamp.nanoseconds // 1_000_000 - self.world_delay_ms,
                   "robot": {"pose": {"x": self.base.x, "y": self.base.y},
                             "yaw": self.base.yaw,
                             "twist": {"linear": self.base.speed, "angular": 0.0}},
                   "humans": people, "confidence": 1.0}
        self.world_pub.publish(String(data=json.dumps(payload)))
        self.world_events.append((now, bool(people)))

    def propose(self, linear, malformed=False):
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.twist.linear.x = linear
        if malformed:
            msg.twist.linear.y = 1.0
        self.vla_pub.publish(msg)

    def fault(self, mode):
        stamp = self.get_clock().now().nanoseconds // 1_000_000
        self.fault_pub.publish(String(data=json.dumps({"code": "sim-" + mode,
                                                       "timestamp_ms": stamp,
                                                       "raise_to": mode})))
