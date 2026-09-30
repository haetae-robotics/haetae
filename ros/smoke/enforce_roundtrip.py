#!/usr/bin/env python3
"""Jazzy end-to-end smoke: signed world/VLA/fault → Rust → /cmd_vel.

This tests ROS wiring, not physical stopping distance or SROS2 permissions.
Run under an isolated ROS_DOMAIN_ID with the compiled Linux haetae binary.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import rclpy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from geometry_msgs.msg import Twist, TwistStamped
from std_msgs.msg import String


def public(seed):
    return Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw).hex()


def run(binary, output_stamped=True):
    shared = Path("/dev/shm")
    with tempfile.TemporaryDirectory(dir=shared if shared.is_dir() and os.access(shared, os.W_OK)
                                     else None) as directory:
        root = Path(directory)
        policy = {
            "allowed_sources": ["vla"],
            "freshness": {"world_max_age_ms": 200, "proposal_max_age_ms": 1000,
                          "future_tolerance_ms": 20},
            "envelope": {"max_speed": 1.0, "workspace": {
                "min": {"x": 0, "y": 0}, "max": {"x": 10, "y": 10}}},
            "base": {"footprint_radius": 0.25, "max_decel": 1.0,
                     "max_angular": 1.0, "latency_ms": 50, "max_ttl_ms": 200},
            "rules": [{"id": "person", "when": {"human_within": {"distance": 0.3}},
                       "then": "bul"}],
        }
        policy_bytes = json.dumps(policy, separators=(",", ":")).encode()
        (root / "policy.json").write_bytes(policy_bytes)
        for name, seed in (("world", 2), ("fault", 3), ("vla", 4), ("log", 9)):
            path = root / (name + ".key")
            path.write_text((bytes([seed]) * 32).hex())
            path.chmod(0o600)
        body = json.dumps({"v": 1, "audience": "ros-smoke", "epoch": 1,
                           "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
                           "keys": {"world": public(2), "fault": public(3), "vla": public(4)}},
                          separators=(",", ":"))
        signature = Ed25519PrivateKey.from_private_bytes(bytes([1]) * 32).sign(
            b"haetae-trust-bundle-v1\0" + body.encode()).hex()
        (root / "trust.json").write_text(json.dumps({"body": body, "signature": signature}))
        state_path = root / "state.json"
        subprocess.run([binary, "state", "set", "--state", str(state_path), "--mode", "normal",
                        "--by", "ci", "--reason", "smoke"], check=True)
        params = {"haetae_gate": {"ros__parameters": {
            "haetae_bin": binary, "policy_path": str(root / "policy.json"),
            "state_path": str(state_path), "sillok_path": str(root / "sillok.jsonl"),
            "key_path": str(root / "log.key"), "trust_path": str(root / "trust.json"),
            "root_pubkey": public(1),
            "keys_json": json.dumps({name: str(root / (name + ".key")) for name in ("world", "fault", "vla")}),
            "inputs_json": json.dumps([{"topic": "/vla/cmd_vel", "source": "vla", "ttl_ms": 200}]),
            "response_timeout_ms": 500,
            "output_stamped": output_stamped,
        }}}
        (root / "params.yaml").write_text(json.dumps(params))
        repo = Path(__file__).resolve().parents[2]
        node_script = repo / "ros" / "haetae_gate" / "node.py"
        with (root / "gate.stderr").open("wb") as stderr:
            gate = subprocess.Popen([sys.executable, str(node_script), "--ros-args", "--params-file",
                                     str(root / "params.yaml")], stderr=stderr)
            rclpy.init()
            test = rclpy.create_node("haetae_e2e")
            commands = []
            states = []
            outcomes = []
            output_type = TwistStamped if output_stamped else Twist
            test.create_subscription(output_type, "/cmd_vel", lambda m: commands.append((
                time.monotonic(), (m.twist if output_stamped else m).linear.x)), 10)
            test.create_subscription(String, "/haetae_gate/state", lambda m: states.append(json.loads(m.data)), 10)
            test.create_subscription(String, "/haetae_gate/outcome", lambda m: outcomes.append(json.loads(m.data)), 10)
            world_pub = test.create_publisher(String, "/haetae_gate/world", 10)
            fault_pub = test.create_publisher(String, "/haetae_gate/fault", 10)
            twist_pub = test.create_publisher(TwistStamped, "/vla/cmd_vel", 10)
            people = []
            last_twist = [0.0]

            def world():
                stamp = test.get_clock().now().nanoseconds // 1_000_000
                payload = {"stamp_ms": stamp, "robot": {"pose": {"x": 5.0, "y": 5.0}, "yaw": 0.0},
                           "humans": people, "confidence": 1.0}
                world_pub.publish(String(data=json.dumps(payload)))

            test.create_timer(0.05, world)

            def until(predicate, timeout=5, action=None):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if gate.poll() is not None:
                        raise AssertionError("gate exited early: " + (root / "gate.stderr").read_text())
                    if action:
                        action()
                    rclpy.spin_once(test, timeout_sec=0.02)
                    if predicate():
                        return
                raise AssertionError("timed out; gate log: " + (root / "gate.stderr").read_text())

            def twist(linear):
                if time.monotonic() - last_twist[0] < 0.05:
                    return
                last_twist[0] = time.monotonic()
                msg = TwistStamped()
                msg.header.stamp = test.get_clock().now().to_msg()
                msg.twist.linear.x = linear
                twist_pub.publish(msg)

            try:
                until(lambda: states and states[-1]["mode"] == "normal" and commands)
                until(lambda: states and states[-1]["armed"], action=lambda: twist(0.0))
                until(lambda: any(v > 0.1 for _, v in commands), action=lambda: twist(0.5))
                malformed_at = time.monotonic()
                bad = TwistStamped()
                bad.twist.linear.y = 1.0
                for _ in range(20):
                    twist_pub.publish(bad)
                    rclpy.spin_once(test, timeout_sec=0.01)
                until(lambda: any(ts >= malformed_at and v == 0.0 for ts, v in commands)
                      and any("rejected" in o for o in outcomes))
                assert gate.poll() is None, "malformed proposals killed the gate"
                until(lambda: states and states[-1]["armed"], action=lambda: twist(0.0))
                moving_at = time.monotonic()
                until(lambda: any(ts >= moving_at and v > 0.1 for ts, v in commands),
                      action=lambda: twist(0.5))
                triggered = time.monotonic()
                people.append({"id": "person", "class": "adult", "pos": {"x": 5.9, "y": 5.0}})
                until(lambda: any(ts >= triggered and v == 0.0 for ts, v in commands), timeout=2,
                      action=lambda: twist(0.5))
                latency = next(ts - triggered for ts, v in commands if ts >= triggered and v == 0.0)
                assert latency <= 0.25, "revoke response took %.3f s" % latency
                stamp = test.get_clock().now().nanoseconds // 1_000_000
                fault_pub.publish(String(data=json.dumps({"code": "test-hold", "timestamp_ms": stamp,
                                                          "raise_to": "hold"})))
                until(lambda: states and states[-1]["mode"] == "hold")
                assert commands[-1][1] == 0.0
                print(json.dumps({"ok": True, "revoke_latency_ms": round(latency * 1000),
                                  "commands": len(commands), "output_stamped": output_stamped}))
            finally:
                gate.terminate()
                try:
                    gate.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    gate.kill()
                    gate.wait()
                test.destroy_node()
                rclpy.shutdown()
            if (root / "sillok.jsonl").exists():
                report = subprocess.run([binary, "sillok", "verify", "--log", str(root / "sillok.jsonl"),
                                         "--pubkey", public(9)], capture_output=True, text=True)
                if report.returncode or not json.loads(report.stdout)["fully_sealed"]:
                    raise AssertionError("sillok incomplete: " + report.stdout + report.stderr)


if __name__ == "__main__":
    run(os.path.abspath(sys.argv[1]), output_stamped="--unstamped" not in sys.argv[2:])
