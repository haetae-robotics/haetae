"""ROS 2 smoke test for the W3 adapter contract (docs/w2-contract.md).

Checks the two assumptions the haetae_gate node will rely on:
  1. JSON payloads survive a std_msgs/String round trip on a reliable topic.
  2. The node clock yields a usable millisecond receive time (recv_ms).
"""

import json
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

PROPOSAL = {
    "id": 1,
    "source": "planner",
    "timestamp_ms": 1727241780000,
    "action": {"type": "move_to", "goal": {"x": 3.0, "y": 3.0}, "speed": 0.5},
}


def main() -> int:
    rclpy.init()
    node = Node("haetae_smoke")
    qos = QoSProfile(
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        history=HistoryPolicy.KEEP_LAST,
    )
    received = []

    def on_proposal(msg: String) -> None:
        recv_ms = node.get_clock().now().nanoseconds // 1_000_000
        received.append((json.loads(msg.data), recv_ms))

    node.create_subscription(String, "/haetae_gate/proposal", on_proposal, qos)
    publisher = node.create_publisher(String, "/haetae_gate/proposal", qos)

    deadline = time.monotonic() + 10.0
    while not received and time.monotonic() < deadline:
        publisher.publish(String(data=json.dumps(PROPOSAL)))
        rclpy.spin_once(node, timeout_sec=0.1)

    node.destroy_node()
    rclpy.shutdown()

    if not received:
        print("FAIL: no message received within 10 s")
        return 1
    payload, recv_ms = received[0]
    if payload != PROPOSAL:
        print(f"FAIL: payload changed in transit: {payload!r}")
        return 1
    if recv_ms <= 0:
        print(f"FAIL: node clock gave recv_ms={recv_ms}")
        return 1
    print(f"ok: JSON round trip over std_msgs/String, recv_ms={recv_ms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
