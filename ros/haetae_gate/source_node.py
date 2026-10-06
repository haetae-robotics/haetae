#!/usr/bin/env python3
"""Isolated role signer. It has no controller or gateway-state authority."""
import json
from pathlib import Path

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.event_handler import PublisherEventCallbacks
from geometry_msgs.msg import TwistStamped
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory

from signing import Signer
from counter_store import CounterStore
from proposals import InvalidProposal, base_action, arm_action, semantic_binding


class SourceSigner(Node):
    def __init__(self):
        super().__init__("haetae_source_signer")
        for name, default in (("role", ""), ("trust_path", ""), ("counter_path", ""),
                              ("keys_json", "{}"), ("arm_joints_json", "[]"), ("arm_fixed_ttl_ms", 0)):
            self.declare_parameter(name, default)
        param = lambda key: self.get_parameter(key).value
        self.role = param("role")
        self.arm_fixed_ttl_ms = param("arm_fixed_ttl_ms")
        if type(self.arm_fixed_ttl_ms) is not int or self.arm_fixed_ttl_ms not in (0, 1000):
            raise ValueError("unsupported fixed arm lease")
        keys = json.loads(param("keys_json"))
        expected = {"world", "fault"} if self.role == "world" else {"vla"}
        if self.role not in ("world", "vla") or set(keys) != expected:
            raise ValueError("source signer must own exactly its role keys")
        self.counter_path = Path(param("counter_path"))
        self.store = CounterStore(self.counter_path)
        self.signer = Signer(param("trust_path"), self.counter_path, keys)
        self.seq = self.signer.counters.get("vla", 0)
        self.joints = json.loads(param("arm_joints_json"))
        self.pubs = {role: self.create_publisher(String, "/haetae_gate/signed/" + role,
                     QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
                     if role == "world" else 1,
                     event_callbacks=PublisherEventCallbacks(matched=self.vla_matched)
                     if role == "vla" else None) for role in keys}
        if self.role == "world":
            self.create_subscription(String, "/haetae_input/world",
                                     lambda msg: self.send("world", msg.data),
                                     QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
            self.create_subscription(String, "/haetae_input/fault",
                                     lambda msg: self.send("fault", msg.data), 1)
        else:
            self.create_subscription(TwistStamped, "/vla/cmd_vel",
                                     lambda msg: self.propose(lambda: base_action(msg, 200)), 1)
            self.create_subscription(JointTrajectory, "/vla/arm",
                                     self.propose_arm, 1)

    def propose_arm(self, msg: JointTrajectory):
        self.propose(lambda: arm_action(msg, self.joints, self.arm_fixed_ttl_ms), msg.header.frame_id)

    def vla_matched(self, event):
        self.get_logger().info("VLA matched readers: " + str(event.current_count))

    def send(self, role, payload):
        envelope = self.signer.sign(role, payload)
        # Reserve counters durably before publication, so a restart cannot
        # emit a previously used counter even if the gateway has not seen it.
        self.store.reserve(self.signer.epoch, self.signer.counters)
        self.pubs[role].publish(String(data=json.dumps(envelope, separators=(",", ":"))))
        if role == "vla" and payload.get("action", {}).get("type") == "stop":
            # Post-publication diagnostic, not a delivery acknowledgement.
            # Never log signed envelopes or do logging before publication.
            self.get_logger().info("VLA stop published: proposal_id=" + str(payload["id"])
                + " counter=" + str(envelope["counter"]))

    def propose(self, parse, semantic_frame=""):
        stamp = self.get_clock().now().nanoseconds // 1_000_000
        # A restarted simulation-time node starts at zero until its /clock
        # reader discovers the publisher. Stops may be accepted by the engine
        # at time zero; they must not masquerade as proof this signer is ready.
        if stamp <= 0:
            return
        try:
            action = parse()
            binding = semantic_binding(semantic_frame)
        except InvalidProposal as exc:
            # Malformed source input becomes a signed stop from that source.
            self.get_logger().warning("Rejected proposal: " + str(exc))
            action = {"type": "stop"}
            binding = None
        self.seq += 1
        proposal = {"id": self.seq, "source": "vla", "timestamp_ms": stamp,
                    "action": action}
        if binding is not None and action["type"] != "stop":
            proposal["semantic"] = binding
        self.send("vla", proposal)


def main():
    rclpy.init()
    node = SourceSigner()
    try:
        rclpy.spin(node)
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        node.store.close()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
