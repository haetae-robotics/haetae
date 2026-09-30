#!/usr/bin/env python3
"""Attack the generated SROS2 permissions on a live Jazzy DDS graph.

The VLA enclave is allowed to publish proposals. It tries to publish trusted
world/fault and direct actuator commands, and to call the arm action. Receiver
nodes record what actually arrived; a working authorized path is required so
an entirely disconnected graph cannot pass as secure.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROLES = {"gate": ("/haetae/gate", "haetae_gate"),
         "world": ("/haetae/world", "world_source"),
         "vla": ("/haetae/vla", "vla_source"),
         "controller": ("/haetae/controller", "joint_trajectory_controller")}


def participant(role, output):
    import rclpy
    from control_msgs.action import FollowJointTrajectory
    from geometry_msgs.msg import TwistStamped
    from rclpy.action import ActionClient, ActionServer, GoalResponse
    from rclpy.node import Node
    from std_msgs.msg import String
    from trajectory_msgs.msg import JointTrajectoryPoint

    enclave, name = ROLES[role]
    rclpy.init(args=["--ros-args", "-e", enclave])
    node = Node(name)
    seen = {"world": [], "fault": [], "vla": [], "cmd": [], "goals": [], "denied": []}
    publishers = []
    action_client = None
    goal_sent = False

    def publisher(message_type, topic):
        try:
            pub = node.create_publisher(message_type, topic, 10)
            publishers.append(pub)
            return pub
        except Exception as exc:
            seen["denied"].append(topic + ":" + type(exc).__name__)
            return None

    def action_goal(value):
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = ["shoulder"]
        point = JointTrajectoryPoint()
        point.positions = [value]
        point.time_from_start.nanosec = 100_000_000
        goal.trajectory.points = [point]
        return goal

    if role == "gate":
        node.create_subscription(String, "/haetae_gate/world",
                                 lambda m: seen["world"].append(m.data), 10)
        node.create_subscription(String, "/haetae_gate/fault",
                                 lambda m: seen["fault"].append(m.data), 10)
        node.create_subscription(TwistStamped, "/vla/cmd_vel",
                                 lambda m: seen["vla"].append(m.twist.linear.x), 10)
        direct = publisher(TwistStamped, "/cmd_vel")
        action_client = ActionClient(node, FollowJointTrajectory,
                                     "/joint_trajectory_controller/follow_joint_trajectory")
    elif role == "controller":
        node.create_subscription(TwistStamped, "/cmd_vel",
                                 lambda m: seen["cmd"].append(m.twist.linear.x), 10)

        def accept(goal):
            seen["goals"].append(goal.trajectory.points[0].positions[0])
            return GoalResponse.ACCEPT

        def execute(handle):
            handle.succeed()
            return FollowJointTrajectory.Result()

        action_server = ActionServer(node, FollowJointTrajectory,
                                      "/joint_trajectory_controller/follow_joint_trajectory",
                                      goal_callback=accept, execute_callback=execute)
    elif role == "world":
        world_pub = publisher(String, "/haetae_gate/world")
        fault_pub = publisher(String, "/haetae_gate/fault")
    else:
        vla_pub = publisher(TwistStamped, "/vla/cmd_vel")
        rogue_cmd = publisher(TwistStamped, "/cmd_vel")
        rogue_world = publisher(String, "/haetae_gate/world")
        rogue_fault = publisher(String, "/haetae_gate/fault")
        try:
            action_client = ActionClient(node, FollowJointTrajectory,
                                         "/joint_trajectory_controller/follow_joint_trajectory")
        except Exception as exc:
            seen["denied"].append("arm-action:" + type(exc).__name__)

    started = time.monotonic()
    next_send = started + 0.5
    while time.monotonic() - started < 3.0:
        now = time.monotonic()
        if now >= next_send:
            next_send = now + 0.05
            if role == "gate" and direct:
                msg = TwistStamped()
                msg.twist.linear.x = 1.0
                direct.publish(msg)
            elif role == "world":
                if world_pub:
                    world_pub.publish(String(data="trusted-world"))
                if fault_pub:
                    fault_pub.publish(String(data="trusted-fault"))
            elif role == "vla":
                normal = TwistStamped()
                normal.twist.linear.x = 0.5
                if vla_pub:
                    vla_pub.publish(normal)
                attack = TwistStamped()
                attack.twist.linear.x = 9.0
                if rogue_cmd:
                    rogue_cmd.publish(attack)
                if rogue_world:
                    rogue_world.publish(String(data="rogue-world"))
                if rogue_fault:
                    rogue_fault.publish(String(data="rogue-fault"))
        if role in ("gate", "vla") and action_client and not goal_sent:
            try:
                if action_client.wait_for_server(timeout_sec=0.0):
                    action_client.send_goal_async(action_goal(0.1 if role == "gate" else 9.0))
                    goal_sent = True
            except Exception as exc:
                seen["denied"].append("arm-action:" + type(exc).__name__)
                goal_sent = True
        rclpy.spin_once(node, timeout_sec=0.01)
    seen["action_sent"] = goal_sent
    Path(output).write_text(json.dumps(seen))
    node.destroy_node()
    rclpy.shutdown()


def orchestrate(store):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        common = os.environ.copy()
        common.update({"ROS_SECURITY_ENABLE": "true", "ROS_SECURITY_STRATEGY": "Enforce",
                       "ROS_SECURITY_KEYSTORE": str(Path(store).resolve())})
        children = {}
        try:
            for role in ("controller", "gate", "world", "vla"):
                out = root / (role + ".json")
                err = (root / (role + ".stderr")).open("wb")
                proc = subprocess.Popen([sys.executable, __file__, role, str(out)],
                                        env=common, stderr=err)
                children[role] = (proc, err, out)
                if role == "gate":
                    time.sleep(0.15)
            for role, (proc, _, _) in children.items():
                if proc.wait(timeout=8) != 0:
                    raise AssertionError(role + " exited with " + str(proc.returncode))
            result = {role: json.loads(out.read_text()) for role, (_, _, out) in children.items()}
            gate = result["gate"]
            controller = result["controller"]
            required = {"trusted world": "trusted-world" in gate["world"],
                        "trusted fault": "trusted-fault" in gate["fault"],
                        "VLA proposal": 0.5 in gate["vla"],
                        "gate base command": 1.0 in controller["cmd"],
                        "gate arm action": 0.1 in controller["goals"],
                        "forged world blocked": "rogue-world" not in gate["world"],
                        "forged fault blocked": "rogue-fault" not in gate["fault"],
                        "direct base blocked": 9.0 not in controller["cmd"],
                        "direct arm blocked": 9.0 not in controller["goals"]}
            failed = [name for name, passed in required.items() if not passed]
            if failed:
                raise AssertionError("SROS2 permission probe failed: " + ", ".join(failed))
            print(json.dumps({"ok": True, "authorized": {"world": True, "fault": True,
                  "proposal": True, "base": True, "arm": True},
                  "unauthorized_received": {
                      "direct_base": controller["cmd"].count(9.0),
                      "direct_arm": controller["goals"].count(9.0),
                      "forged_world": gate["world"].count("rogue-world"),
                      "forged_fault": gate["fault"].count("rogue-fault")},
                  "vla_denied": result["vla"]["denied"]}))
        finally:
            for role, (proc, err, _) in children.items():
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                err.close()
                content = (root / (role + ".stderr")).read_text()
                if content:
                    print(role + " stderr:\n" + content, file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "orchestrate":
        orchestrate(sys.argv[2])
    else:
        participant(sys.argv[1], sys.argv[2])
