"""Household hazard evaluation with real Gazebo joint feedback.

The root-owned adapter and injected semantic fixtures are trusted. Native props
are kinematic fixtures, not validated grasping, chemistry or camera perception.
No untrusted joint/path pair is accepted by this lab: it derives the checked
path from the exact immutable plan submitted through the existing signed gate.
"""

import hashlib
import json
import math
import multiprocessing
import select
from typing import Callable, Literal, Optional, Union
from pathlib import Path
import subprocess
import threading
import time
from xml.etree import ElementTree as E

CASES = [
    ("human", "사람 보호 공간", "knife", "human", "human:protected-volume"),
    ("heat", "가스통과 열원", "pressurized", "heat", "heat:hazardous-item"),
    (
        "electricity",
        "금속 도구와 전기 기기",
        "conductive",
        "electrical",
        "electricity:contact",
    ),
    ("water", "배터리와 물", "battery", "water", "water:electrical-item"),
    (
        "chemicals",
        "세정제 혼합과 작업 이력",
        "ammonia",
        "container",
        "chemicals:incompatible",
    ),
    ("fall", "추락 공간 접근", "inert", "fall", "fall:protected-volume"),
]
PLAN_TTL_MS = 1000
ROBOT_ID = "rosbot_xl_open_manipulator_x"
TOOL_ID = "household_fixture_v1"


def lab_posture(urdf):
    root = E.fromstring(urdf)
    for name, value in (("joint2", 0.4), ("joint3", -0.4), ("joint4", 0)):
        state = root.find(
            f"ros2_control/joint[@name='{name}']/state_interface[@name='position']/param[@name='initial_value']"
        )
        if state is None:
            raise ValueError("missing initial joint state")
        state.text = str(value)
    return E.tostring(root, encoding="unicode")


def add_fixtures(world):
    fixtures = json.loads(
        (
            Path(__file__).resolve().parents[2] / "sim/assets/household-fixtures.json"
        ).read_text()
    )
    for case, *_ in CASES:
        for role in ("target", "item"):
            model = E.SubElement(world, "model", name=f"hazard_{case}_{role}")
            E.SubElement(model, "static").text = "true"
            E.SubElement(model, "pose").text = "0 0 -5 0 0 0"
            link = E.SubElement(model, "link", name="body")
            for index, part in enumerate(fixtures[case][role]):
                visual = E.SubElement(link, "visual", name=f"part_{index}")
                E.SubElement(visual, "pose").text = " ".join(
                    map(str, [*part["pos"], 0, 0, 0])
                )
                shape = E.SubElement(E.SubElement(visual, "geometry"), part["shape"])
                for name in ("size", "radius", "length"):
                    if name in part:
                        E.SubElement(shape, name).text = (
                            " ".join(map(str, part[name]))
                            if isinstance(part[name], list)
                            else str(part[name])
                        )
                rgb = [int(part["color"][n : n + 2], 16) / 255 for n in (1, 3, 5)]
                material = E.SubElement(visual, "material")
                for tag in ("ambient", "diffuse"):
                    E.SubElement(material, tag).text = " ".join(map(str, [*rgb, 1]))


def multiply(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)
    ]


def transform(xyz=(0, 0, 0), rpy=(0, 0, 0)):
    r, p, y = rpy
    cr, sr, cp, sp, cy, sy = (
        math.cos(r),
        math.sin(r),
        math.cos(p),
        math.sin(p),
        math.cos(y),
        math.sin(y),
    )
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, xyz[0]],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, xyz[1]],
        [-sp, cp * sr, cp * cr, xyz[2]],
        [0, 0, 0, 1],
    ]


class Kinematics:
    def __init__(self, urdf):
        root = E.fromstring(urdf)
        joints = {
            j.find("child").get("link"): j
            for j in root.findall("joint")
            if j.find("child") is not None
        }
        self.chain = []
        link = "end_effector_link"
        while link != "base_link":
            joint = joints[link]
            self.chain.insert(0, joint)
            link = joint.find("parent").get("link")
        # This adapter deliberately supports only this pinned product's axes.
        for j in self.chain:
            if j.get("type") != "fixed" and j.get("name") not in (
                "joint1",
                "joint2",
                "joint3",
                "joint4",
            ):
                raise ValueError("unsupported kinematic joint")

    def frame(self, positions, pose, yaw):
        matrix = transform((*pose, 0), (0, 0, yaw))
        q = dict(zip(("joint1", "joint2", "joint3", "joint4"), positions))
        for joint in self.chain:
            origin = joint.find("origin")
            xyz = (
                [float(v) for v in origin.get("xyz", "0 0 0").split()]
                if origin is not None
                else [0] * 3
            )
            rpy = (
                [float(v) for v in origin.get("rpy", "0 0 0").split()]
                if origin is not None
                else [0] * 3
            )
            matrix = multiply(matrix, transform(xyz, rpy))
            if joint.get("type") != "fixed":
                axis = [float(v) for v in joint.find("axis").get("xyz").split()]
                angle = q[joint.get("name")]
                if axis == [0, 0, 1]:
                    rotation = (0, 0, angle)
                elif axis == [0, 1, 0]:
                    rotation = (0, angle, 0)
                else:
                    raise ValueError("unsupported kinematic axis")
                matrix = multiply(matrix, transform(rpy=rotation))
        return matrix

    def point(self, positions, pose, yaw):
        matrix = self.frame(positions, pose, yaw)
        return {"x": matrix[0][3], "y": matrix[1][3], "z": matrix[2][3]}

    def household_policy(self, urdf):
        """Root-provisioned FK data bound into the signed policy bytes."""
        chain = []
        for joint in self.chain:
            origin = joint.find("origin")
            segment: dict[str, object] = {key: [float(v) for v in origin.get(key, "0 0 0").split()]
                       if origin is not None else [0.0, 0.0, 0.0]
                       for key in ("xyz", "rpy")}
            segment.update(joint_index=None, axis=None)
            if joint.get("type") != "fixed":
                segment["joint_index"] = ("joint1", "joint2", "joint3", "joint4").index(joint.get("name"))
                axis = [float(v) for v in joint.find("axis").get("xyz").split()]
                if axis not in ([0, 0, 1], [0, 1, 0]):
                    raise ValueError("unsupported household policy axis")
                segment["axis"] = "z" if axis == [0, 0, 1] else "y"
            chain.append(segment)
        return {"schema_version": 1, "robot_id": ROBOT_ID,
                "model_sha256": hashlib.sha256(urdf.encode()).hexdigest(),
                "tool_id": TOOL_ID, "chain": chain,
                "sample_step_rad": 0.002, "swept_radius_m": 0.15}

    def orientation(self, positions, pose, yaw):
        m = self.frame(positions, pose, yaw)
        trace = m[0][0] + m[1][1] + m[2][2]
        if trace > 0:
            s = math.sqrt(trace + 1) * 2
            q = (
                (m[2][1] - m[1][2]) / s,
                (m[0][2] - m[2][0]) / s,
                (m[1][0] - m[0][1]) / s,
                s / 4,
            )
        else:
            i = max(range(3), key=lambda k: m[k][k])
            j = (i + 1) % 3
            k = (i + 2) % 3
            s = math.sqrt(1 + m[i][i] - m[j][j] - m[k][k]) * 2
            values = [0, 0, 0, 0]
            values[i] = s / 4
            values[j] = (m[j][i] + m[i][j]) / s
            values[k] = (m[k][i] + m[i][k]) / s
            values[3] = (m[k][j] - m[j][k]) / s
            q = tuple(values)
        return q

    def path(self, points, pose, yaw):
        result = []
        for (_, start), (_, end) in zip(points, points[1:]):
            count = max(
                1, math.ceil(max(abs(a - b) for a, b in zip(start, end)) / 0.002)
            )
            for n in range(count):
                result.append(
                    self.point(
                        [a + (b - a) * n / count for a, b in zip(start, end)], pose, yaw
                    )
                )
        result.append(self.point(points[-1][1], pose, yaw))
        return result


def native_fixture_driver(commands, acknowledgements, stop):
    # Gazebo's blocking Python transport call can hold the GIL. A spawned
    # process keeps it away from ROS observation and watchdog processing.
    from gz.transport13 import Node
    from native_person import _set_poses
    from queue import Empty

    node = Node()
    while not stop.is_set():
        try:
            identifier, poses = commands.get(timeout=0.1)
        except Empty:
            continue
        deadline = time.monotonic() + 2
        acknowledged = False
        while not stop.is_set() and time.monotonic() < deadline:
            if _set_poses(node, poses):
                acknowledged = True
                break
            time.sleep(0.01)
        acknowledgements.put((identifier, acknowledged))


class NativeFixtures:
    def __init__(self):
        from gz.transport13 import Node
        from gz.msgs10.pose_v_pb2 import Pose_V

        self.node = Node()
        self.lock = threading.Lock()
        self.poses = {}
        self.command_lock = threading.Lock()
        self.failure = None
        context = multiprocessing.get_context("spawn")
        self.commands = context.Queue(maxsize=1)
        self.acknowledgements = context.Queue(maxsize=1)
        self.stop = context.Event()
        self.sequence = 0
        self.process = context.Process(
            target=native_fixture_driver,
            args=(self.commands, self.acknowledgements, self.stop),
            daemon=True,
        )
        self.process.start()
        self.node.subscribe(Pose_V, "/world/empty/pose/info", self.observe)

    def observe(self, msg):
        stamp = msg.header.stamp.sec * 1000 + msg.header.stamp.nsec // 1_000_000
        with self.lock:
            for p in msg.pose:
                if p.name.startswith("hazard_"):
                    self.poses[p.name] = (
                        {"x": p.position.x, "y": p.position.y, "z": p.position.z},
                        stamp,
                        time.monotonic(),
                        (
                            p.orientation.x,
                            p.orientation.y,
                            p.orientation.z,
                            p.orientation.w,
                        ),
                    )

    def sample(self, name):
        with self.lock:
            return self.poses.get(name)

    def set(self, poses, orientations=None):
        from queue import Empty

        values = {
            name: (
                (p["x"], p["y"], p["z"]),
                (orientations or {}).get(name, (0, 0, 0, 1)),
            )
            for name, p in poses.items()
        }
        with self.command_lock:
            if not self.process.is_alive():
                raise RuntimeError("native fixture driver exited")
            self.sequence += 1
            identifier = self.sequence
            self.commands.put((identifier, values), timeout=0.2)
            try:
                received, acknowledged = self.acknowledgements.get(timeout=2.5)
            except Empty as exc:
                raise RuntimeError(
                    "native hazard fixture pose was not acknowledged"
                ) from exc
            if received != identifier or not acknowledged:
                raise RuntimeError("native hazard fixture pose was not acknowledged")

    def close(self):
        self.stop.set()
        self.process.join(timeout=3)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
        self.node.unsubscribe("/world/empty/pose/info")
        self.commands.close()
        self.acknowledgements.close()


def fresh_sample(sample, now_ms, wall_now):
    return bool(
        sample and -20 <= now_ms - sample[1] <= 200 and 0 <= wall_now - sample[2] <= 0.2
    )


def stationary(before, after, before_yaw, after_yaw, speed):
    return (
        math.dist(before, after) <= 0.002
        and abs(
            math.atan2(
                math.sin(after_yaw - before_yaw), math.cos(after_yaw - before_yaw)
            )
        )
        <= 0.01
        and abs(speed) < 0.01
    )


def ordinary_completion(state):
    return (
        state.get("mode") == "normal"
        and state.get("stop") in ("no_command", "startup")
        and state.get("active") is None
        and not state.get("arm_cancelling")
    )


def accepted_plan_matches(points, action):
    rows = action.get("points", [])
    return (
        action.get("type") == "joint_trajectory"
        and action.get("ttl_ms") == PLAN_TTL_MS
        and len(rows) == len(points)
        and all(
            row.get("time_from_start_ms") == millis
            and len(row.get("positions", [])) == len(values)
            and all(abs(a - b) <= 1e-12 for a, b in zip(row["positions"], values))
            for row, (millis, values) in zip(rows, points)
        )
    )


def expected_positions(points, elapsed):
    for (start_ms, start), (end_ms, end) in zip(points, points[1:]):
        if elapsed <= end_ms:
            fraction = max(0, min(1, (elapsed - start_ms) / (end_ms - start_ms)))
            return [a + (b - a) * fraction for a, b in zip(start, end)]
    return points[-1][1]


def plan(start, target):
    # Immutable tuples are used for both FK certification and submission.
    end = (float(target), *map(float, start[1:]))
    # Finish before the fixed 1s lease expires: otherwise expiry cancellation
    # races the action server's normal completion/result delivery.
    return ((0, tuple(map(float, start))), (800, end), (900, end))


def tracking_start_ms(decision):
    # The gate decision expires at approval time + action TTL, not at the
    # last waypoint. See Enforcer::process_proposal and Runtime::approve.
    return decision["expires_ms"] - decision["action"]["ttl_ms"]


class HazardJudge:
    """One bounded JSONL judge process; no hot-path process creation."""

    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary, "hazard-judge"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def decide(self, request):
        self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 2)[0]:
            raise TimeoutError("hazard judge response missing")
        value = json.loads(self.process.stdout.readline(4096))
        if type(value.get("allowed")) is not bool or not isinstance(
            value.get("reason"), str
        ):
            raise ValueError("invalid hazard decision")
        return value

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process.stdout.close()


def run_lab(world, binary, urdf, root, processes, wait_for, review_scene):
    if not world.isolated:
        raise ValueError("household lab requires the isolated signed graph")
    native = NativeFixtures()
    kinematics = Kinematics(urdf)
    rows = []
    active = {
        "case": None,
        "path": [],
        "stage": 0,
        "title": "생활 위험 실험",
        "contents": [],
        "item_kind": None,
    }
    timer = None
    tracking_timer = None
    tracking = {
        "points": None,
        "accepted_after": 0,
        "max_error": 0.0,
        "samples": 0,
        "last_stamp": None,
        "accepted": None,
    }
    attachment_stop = threading.Event()
    attachment_thread = None
    completed = False
    judge_engine = None
    motion_guard = {"active": False, "base": None, "yaw": None, "target": None}
    semantic_lock = threading.RLock()
    semantic_context = {"item": None, "contents": [], "revision": 0,
                        "task_revision": 0, "facts": None, "task_id": None,
                        "base_pose": None, "base_yaw": None}
    verified_denial: dict[str, Optional[float]] = {"at": None}
    policy_binding = kinematics.household_policy(urdf)
    world.hazard_guard = lambda: not motion_guard["active"] or healthy()

    def now():
        return world.get_clock().now().nanoseconds // 1_000_000

    def publish():
        case = active["case"]
        if not case:
            return
        item = native.sample(f"hazard_{case}_item")
        target = native.sample(f"hazard_{case}_target")
        if not item or not target:
            return
        world._emit(
            "hazard_scene",
            stage=active["stage"],
            title=active["title"],
            case=case,
            item=item[0],
            item_quaternion=item[3],
            target=target[0],
            path=active["path"],
            contents=active["contents"],
            observed_ms=min(item[1], target[1]),
            fixture_states=True,
            item_kind=active["item_kind"],
            same_visual_prop=case == "chemicals",
        )

    def healthy():
        sample = native.sample(f"hazard_{active['case']}_target")
        if (
            native.failure
            or not native.process.is_alive()
            or not fresh_sample(sample, now(), time.monotonic())
        ):
            return False
        return (
            stationary(
                motion_guard["base"],
                world.pose(),
                motion_guard["yaw"],
                world.heading(),
                world.speed(),
            )
            and math.dist(sample[0].values(), motion_guard["target"].values()) <= 0.002
        )

    def monitor_tracking():
        if tracking["points"] is None or world.joint is None:
            return
        accepted = tracking["accepted"] or next(
            (
                row.get("decision", {})
                for t, row in world.outcomes
                if t >= tracking["accepted_after"]
                and row.get("decision", {}).get("verdict") == "yun"
                and accepted_plan_matches(
                    tracking["points"], row.get("decision", {}).get("action", {})
                )
            ),
            None,
        )
        if not accepted or "expires_ms" not in accepted:
            return
        tracking["accepted"] = accepted
        stamp = (
            world.joint.header.stamp.sec * 1000
            + world.joint.header.stamp.nanosec // 1_000_000
        )
        start = tracking_start_ms(accepted)
        if (
            stamp < start
            or stamp > accepted["expires_ms"]
            or stamp == tracking["last_stamp"]
        ):
            return
        desired = expected_positions(tracking["points"], stamp - start)
        error = max(abs(a - b) for a, b in zip(world.arm_positions(), desired))
        tracking["max_error"] = max(tracking["max_error"], error)
        tracking["samples"] += 1
        tracking["last_stamp"] = stamp
        if error > 0.05:
            native.failure = "measured joint tracking exceeded .05 rad"

    def attachment(case):
        # Native service calls must never occupy the ROS callback group.
        while not attachment_stop.wait(0.05):
            try:
                joints, pose, yaw = world.arm_positions(), world.pose(), world.heading()
                name = f"hazard_{case}_item"
                native.set(
                    {name: kinematics.point(joints, pose, yaw)},
                    {name: kinematics.orientation(joints, pose, yaw)},
                )
            except Exception as exc:
                native.failure = str(exc)
                return

    def request(item, points, contents=()):
        case = active["case"]
        sample = native.sample(f"hazard_{case}_target")
        item_sample = native.sample(f"hazard_{case}_item")
        if (
            native.failure
            or not fresh_sample(sample, now(), time.monotonic())
            or not fresh_sample(item_sample, now(), time.monotonic())
            or time.monotonic() - world.joint_received > 0.2
            or time.monotonic() - world.odom_received > 0.2
        ):
            raise RuntimeError("hazard geometry or joint feedback stale")
        if abs(world.speed()) >= 0.01:
            raise RuntimeError("base must be stationary")
        position, stamp = sample[:2]
        path = kinematics.path(points, world.pose(), world.heading())
        joint_stamp = (
            world.joint.header.stamp.sec * 1000
            + world.joint.header.stamp.nanosec // 1_000_000
        )
        odom_stamp = (
            world.odom.header.stamp.sec * 1000
            + world.odom.header.stamp.nanosec // 1_000_000
        )
        observed = kinematics.point(
            world.arm_positions(), world.pose(), world.heading()
        )
        return {
            "now_ms": now(),
            "observed_ms": min(stamp, item_sample[1], joint_stamp, odom_stamp),
            "confidence": 1.0,
            "coverage_known": True,
            "item": item,
            "observed_start": observed,
            "swept_radius_m": 0.15,
            "path": path,
            "regions": [
                {
                    "id": "fixture-target",
                    "kind": CASES[active["stage"] - 1][3],
                    "state": "active",
                    "bounds": {
                        "min": {
                            k: v - (0.09 if k == "z" else 0.06)
                            for k, v in position.items()
                        },
                        "max": {
                            k: v + (0.09 if k == "z" else 0.06)
                            for k, v in position.items()
                        },
                    },
                    "contents": list(contents),
                    "contents_known": True,
                }
            ],
        }

    def set_semantic_context(item, contents=()):
        # Only this root-owned fixture transaction can set material/device
        # facts. The untrusted proposal writer receives references only.
        with semantic_lock:
            task_id = "household-lab-" + active["case"]
            if (semantic_context["item"] != item
                    or semantic_context["contents"] != list(contents)
                    or semantic_context["task_id"] != task_id):
                semantic_context.update(item=item, contents=list(contents), task_id=task_id,
                    task_revision=semantic_context["task_revision"] + 1,
                    base_pose={"x": world.pose()[0], "y": world.pose()[1]},
                    base_yaw=world.heading())

    def semantic_snapshot(fused_stamp_ms):
        with semantic_lock:
            if not active["case"] or semantic_context["item"] is None:
                return None
            try:
                positions = world.arm_positions()
                measured = request(semantic_context["item"], ((0, positions), (1, positions)),
                                   semantic_context["contents"])
            except (RuntimeError, ValueError, KeyError):
                return None
            facts = {"schema_version": 1, "task_revision": semantic_context["task_revision"],
                     "task_id": semantic_context["task_id"], "step_id": "fixture-motion",
                     "robot_id": policy_binding["robot_id"],
                     "model_sha256": policy_binding["model_sha256"],
                     "tool_id": policy_binding["tool_id"],
                     "item_id": "hazard_" + active["case"] + "_item",
                     "item": measured["item"], "regions": measured["regions"],
                     "base_pose": semantic_context["base_pose"], "base_yaw": semantic_context["base_yaw"]}
            fingerprint = json.dumps(facts, sort_keys=True, separators=(",", ":"))
            if semantic_context["facts"] != fingerprint:
                semantic_context["facts"] = fingerprint
                semantic_context["revision"] += 1
            return {**facts, "revision": semantic_context["revision"],
                    # Fuse semantic fixtures with the same oldest mechanical/
                    # lidar observation as the outer world; never renew time.
                    "observed_ms": min(measured["observed_ms"], fused_stamp_ms), "confidence": 1.0,
                    "coverage_known": True}

    def current_binding(item, contents=()):
        set_semantic_context(item, contents)

        def accepted_context():
            snapshot = world.semantic_last
            if not snapshot or not world.states:
                return False
            state = world.states[-1][1]
            with semantic_lock:
                return (snapshot["task_revision"] == semantic_context["task_revision"]
                        and state.get("semantic_revision") == snapshot["revision"]
                        and state.get("semantic_task_revision") == snapshot["task_revision"])

        wait_for(accepted_context, 3, processes, "trusted household snapshot accepted by Rust gate")
        snapshot = dict(world.semantic_last)
        fields = ("schema_version", "task_revision", "task_id", "step_id", "robot_id",
                  "model_sha256", "tool_id", "item_id")
        return {**{key: snapshot[key] for key in fields}, "world_revision": snapshot["revision"]}

    def rearm_for_test():
        def ready_for_explicit_test_rearm():
            if not world.states:
                return False
            state = world.states[-1][1]
            expected_rejection = (verified_denial["at"] is not None
                                  and world.states[-1][0] >= verified_denial["at"]
                                  and state.get("mode") == "normal"
                                  and state.get("stop") == "denied"
                                  and state.get("active") is None
                                  and not state.get("arm_cancelling"))
            return ordinary_completion(state) or expected_rejection

        wait_for(ready_for_explicit_test_rearm, 3, processes,
                 "completed motion or verified expected rejection before explicit test rearm")
        rearm_at = time.monotonic()
        wait_for(lambda: world.states and "vla" in world.states[-1][1].get("armed", [])
                 and world.controllers_unlocked()
                 and sum(t >= rearm_at and row.get("decision", {}).get("verdict") == "yun"
                         and row.get("decision", {}).get("action", {}).get("type") == "stop"
                         for t, row in world.outcomes) >= 2,
                 3, processes, "explicit signed zero-command rearm for fixed test",
                 action=lambda: world.propose_base(0.0))
        verified_denial["at"] = None
        drain_until = now() + 150
        wait_for(lambda: now() >= drain_until, 2, processes, "rearm queue drains")

    def reject_at_gate(points, item, contents=(), expected=None,
                       binding_override: Union[dict, Callable[[dict], dict], None, Literal[False]] = False):
        rearm_for_test()
        # STOP/rearm queue draining can admit a newer native scene. Bind only
        # after that preparation, immediately before the signed proposal.
        binding = current_binding(item, contents)
        if binding_override is not False:
            binding = binding_override(binding) if callable(binding_override) else binding_override
        before = world.arm_positions()
        submitted_at = time.monotonic()
        world.propose_arm_plan(points, semantic=binding)

        def rejection():
            for t, row in world.outcomes:
                decision = row.get("decision", {})
                if t >= submitted_at and decision.get("verdict") == "bul" and (
                        expected is None or expected in decision.get("fired", [])):
                    return decision
            return None

        denied = wait_for(rejection, 3, processes, "signed proposal rejected by mandatory Rust household gate")
        time.sleep(0.4)
        drift = max(abs(a - b) for a, b in zip(before, world.arm_positions()))
        if drift > 0.02 or any(t >= submitted_at and row.get("decision", {}).get("verdict") == "yun"
                              and row.get("decision", {}).get("action", {}).get("type") == "joint_trajectory"
                              for t, row in world.outcomes):
            raise AssertionError("rejected signed plan acquired actuator authority")
        verified_denial["at"] = submitted_at
        return {"allowed": False, "reason": expected or denied["fired"][0],
                "signed_gate_rejection_observed": True, "denied_drift_rad": drift}

    def execute(points, item, contents=()):
        # A completed arm lease intentionally unarms the source. This fixed
        # test explicitly requests a new lease only after ordinary completion;
        # a stale/denied/revoked/faulted gate is never automatically rearmed.
        rearm_for_test()
        # A stale local preflight is discarded before any actuator proposal.
        # Reacquire its observations/binding/verdict, boundedly, while the
        # already explicit rearm remains valid. Never retry a dispatched goal
        # or automatically rearm a denied/expired owner.
        preflight = []
        for attempt in range(3):
            state = world.states[-1][1] if world.states else {}
            if (state.get("mode") != "normal" or "vla" not in state.get("armed", [])
                    or state.get("active") is not None or state.get("arm_cancelling")
                    or not world.controllers_unlocked()):
                raise RuntimeError("household preflight lost explicit arm authority")
            binding = current_binding(item, contents)
            checked = request(item, points, contents)
            base, yaw = world.pose(), world.heading()
            target = native.sample(f"hazard_{active['case']}_target")[0]
            started_wall = time.monotonic()
            verdict = judge_engine.decide(checked)
            if not verdict["allowed"]:
                raise AssertionError("positive control rejected: " + verdict["reason"])
            after = native.sample(f"hazard_{active['case']}_target")
            if (max(abs(a-b) for a,b in zip(points[0][1], world.arm_positions())) > 0.01
                    or not stationary(base, world.pose(), yaw, world.heading(), world.speed())
                    or after is None or math.dist(after[0].values(), target.values()) > 0.002):
                raise RuntimeError("household scene changed before dispatch")
            observed_wall = time.monotonic()
            stale = (observed_wall-started_wall > 0.05
                     or not fresh_sample(after, now(), observed_wall)
                     or now()-checked["observed_ms"] > 200)
            preflight.append({"attempt": attempt+1, "stale": stale,
                              "judge_wall_ms": (observed_wall-started_wall)*1000,
                              "observation_age_sim_ms": now()-checked["observed_ms"]})
            if not stale:
                break
            (root / "hazard-preflight.json").write_text(json.dumps(preflight, indent=2))
        else:
            raise RuntimeError("household preflight freshness unavailable after three fresh checks")
        before = world.primary_joint()
        accepted_after = time.monotonic()
        motion_guard.update(active=True, base=base, yaw=yaw, target=target)
        fingerprint = hashlib.sha256(json.dumps(points).encode()).hexdigest()
        tracking.update(
            points=points,
            accepted_after=accepted_after,
            max_error=0.0,
            samples=0,
            last_stamp=None,
            accepted=None,
        )
        if time.monotonic() - started_wall > 0.05:
            raise RuntimeError("hazard dispatch preparation exceeded 50 ms")
        rejected_before = world.guard_states[-1][1]["rejected"]
        world.propose_arm_plan(points, semantic=binding)
        (root / "hazard-preflight.json").write_text(json.dumps(preflight, indent=2))
        goal = points[-1][1][0]
        wait_for(
            lambda: abs(world.primary_joint() - goal) < 0.02
            and time.monotonic() - world.joint_received < 0.2,
            6,
            processes,
            "hazard normal control reaches measured joint goal",
        )
        wait_for(
            lambda: all(
                abs(v) < 0.03
                for j, v in zip(world.joint.name, world.joint.velocity)
                if j.startswith("joint")
            ),
            3,
            processes,
            "hazard normal control settles",
        )
        wait_for(
            lambda: world.states
            and world.states[-1][0] >= accepted_after
            and world.states[-1][1].get("active") is None
            and not world.states[-1][1].get("arm_cancelling"),
            3,
            processes,
            "signed arm gate finishes cancellation and settling",
        )
        completed_wall = time.monotonic_ns()
        wait_for(lambda: world.guard_states and
                 world.guard_states[-1][1].get("published_wall_ns", 0) >= completed_wall + 120_000_000,
                 2, processes, "fresh controller telemetry after normal completion")
        if world.guard_states[-1][1]["rejected"] != rejected_before:
            raise AssertionError("normal arm completion triggered a permit rejection")
        if not healthy():
            raise AssertionError("hazard scene changed during execution")
        motion_guard["active"] = False
        accepted = [
            row
            for t, row in world.outcomes
            if t >= accepted_after
            and row.get("decision", {}).get("verdict") == "yun"
            and row.get("decision", {}).get("action", {}).get("type")
            == "joint_trajectory"
        ]
        if not accepted or not any(
            accepted_plan_matches(points, r["decision"]["action"]) for r in accepted
        ):
            raise AssertionError("no accepted signed arm plan observed")
        if tracking["samples"] < 10 or tracking["max_error"] > 0.05:
            raise AssertionError("insufficient or invalid measured tracking evidence")
        tracking["points"] = None
        moved = abs(world.primary_joint() - before)
        if moved < 0.1:
            raise AssertionError("normal control did not visibly move")
        return {
            "allowed": True,
            "measured_motion_rad": moved,
            "plan_sha256": fingerprint,
            "signed_arm_acceptance_observed": True,
            "mandatory_semantic_gate": True,
            "accepted_waypoints_match": True,
            "max_joint_tracking_error_rad": tracking["max_error"],
            "tracking_samples": tracking["samples"],
        }

    try:
        judge_engine = HazardJudge(binary)
        world.semantic_snapshot = semantic_snapshot
        timer = world.create_timer(0.08, publish)
        tracking_timer = world.create_timer(0.02, monitor_tracking)
        for index, (case, title, item, kind, expected) in enumerate(CASES, 1):
            active.update(
                case=case,
                title=title,
                stage=index,
                path=[],
                contents=[],
                item_kind=item,
            )
            if attachment_thread:
                attachment_stop.set()
                attachment_thread.join(timeout=3)
                if attachment_thread.is_alive():
                    raise RuntimeError("hazard attachment driver failed to stop")
            attachment_stop.clear()
            parked = {
                f"hazard_{c}_{role}": {"x": 0, "y": 0, "z": -5}
                for c, *_ in CASES
                for role in ("item", "target")
            }
            native.set(parked)
            wait_for(
                lambda: native.sample(f"hazard_{case}_target")
                and native.sample(f"hazard_{case}_target")[0]["z"] < -4,
                3,
                processes,
                "parked native hazard fixtures",
            )
            if abs(world.primary_joint()) > 0.1:
                world.marker(
                    "다음 장면 준비",
                    detail="위험 표적을 치운 뒤 팔을 시작 위치로 돌립니다.",
                )
                execute(plan(world.arm_positions(), 0), "inert")
            start = world.arm_positions()
            direction = 1 if start[0] < 0.8 else -1
            dangerous = plan(start, start[0] + direction * 0.75)
            goal = kinematics.point(dangerous[-1][1], world.pose(), world.heading())
            poses = {
                f"hazard_{c}_{role}": {"x": 0, "y": 0, "z": -5}
                for c, *_ in CASES
                for role in ("item", "target")
            }
            poses[f"hazard_{case}_target"] = goal
            poses[f"hazard_{case}_item"] = kinematics.point(
                start, world.pose(), world.heading()
            )
            native.set(poses)
            wait_for(
                lambda: native.sample(f"hazard_{case}_target")
                and math.dist(
                    native.sample(f"hazard_{case}_target")[0].values(), goal.values()
                )
                < 0.001,
                3,
                processes,
                "observed native hazard target",
            )
            # Attachment follows measured FK, never the requested path.
            attachment_thread = threading.Thread(
                target=attachment, args=(case,), daemon=True
            )
            attachment_thread.start()
            world.marker(
                title,
                detail="청록 물체는 시험용 부착물입니다. 점선은 제안 경로이며, 판정 전에는 로봇이 움직이지 않습니다.",
            )
            contents = []
            if case == "chemicals":
                active["item_kind"] = "bleach"
                world.marker(
                    "첫 세정제 이동 검사",
                    detail="빈 시험 용기로 첫 성분을 이동합니다. 측정 완료 후에만 시험 이력을 기록합니다.",
                )
                # Keep each one-second chunk slow enough for the unchanged
                # .05-rad tracking contract, including dispatch latency.
                transfer_steps = []
                for offset in (0.375, 0.75):
                    transfer_steps.append(
                        execute(
                            plan(world.arm_positions(), start[0] + direction * offset),
                            "bleach",
                        )
                    )
                first = {**transfer_steps[-1], "steps": transfer_steps}
                # The simulator fixture transaction is committed only after
                # measured completion. No real fluid/chemical observation.
                contents = ["bleach"]
                active["contents"] = contents
                retreat_steps = []
                for offset in (0.375, 0.0):
                    retreat_steps.append(
                        execute(
                            plan(world.arm_positions(), start[0] + direction * offset),
                            "bleach",
                            contents,
                        )
                    )
                retreat_result = {**retreat_steps[-1], "steps": retreat_steps}
                review_scene(
                    "첫 세정제 시험 이력 기록",
                    "표백 성분 이동을 측정했습니다. 다음 시험은 동일한 병 모형의 주입 종류를 암모니아 성분으로 바꿉니다. 실제 액체 이동·혼합 시험은 아닙니다.",
                    "두 번째 성분 계획 검사",
                )
                active["item_kind"] = "ammonia"
                dangerous = plan(world.arm_positions(), start[0] + direction * 0.75)
            checked = request(item, dangerous, contents)
            active["path"] = checked["path"][::10] + [checked["path"][-1]]
            verdict = judge_engine.decide(checked)
            if verdict["allowed"] or verdict["reason"] != expected:
                raise AssertionError(f"{case}: expected {expected}, got {verdict}")
            gate_denial = reject_at_gate(dangerous, item, contents, "household:" + expected)
            drift = gate_denial["denied_drift_rad"]
            world._emit(
                "hazard_decision",
                **verdict,
                label=title + " · 위험 계획 차단",
                drift_rad=drift,
            )
            review_scene(
                title + " · 차단 장면",
                "유효하게 서명된 위험 명령을 중앙 Rust 게이트가 차단했습니다. 다음에는 정상 명령을 명시적으로 재검사해 실행합니다.",
                "정상 동작 대조",
            )
            safe = plan(world.arm_positions(), world.primary_joint() - direction * 0.3)
            safe_path = kinematics.path(safe, world.pose(), world.heading())
            active["path"] = safe_path[::10] + [safe_path[-1]]
            world.marker(
                title + " · 정상 경로 검사",
                detail="위험 공간을 유지한 채 반대 방향의 계획을 검사합니다.",
            )
            control = execute(safe, item, contents)
            world._emit(
                "hazard_decision",
                allowed=True,
                reason="control-measured",
                label=title + " · 정상 이동 확인",
                **{k: v for k, v in control.items() if k != "allowed"},
            )
            row = {
                "id": case,
                "title": title,
                "blocked": True,
                "reason": verdict["reason"],
                "denied_drift_rad": drift,
                "signed_gate_rejection_observed": True,
                **control,
            }
            if case == "chemicals":
                row["first_transfer"] = first
                row["retreat"] = retreat_result
                row["retained_contents"] = contents
                row["item_label_sequence"] = ["bleach", "ammonia"]
                row["same_visual_prop"] = True
            rows.append(row)
            (root / "hazard-progress.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2)
            )
            review_scene(
                title + " · 정상 장면",
                "위험 공간은 그대로 두고 다른 경로로 팔이 실제 움직였습니다. 물체 종류·기기 상태·용기 내용물은 신뢰된 시험 데이터입니다.",
                CASES[index][1] if index < 6 else "검증 결과",
            )
        # Unknown/stale facts are independent negative controls, never actuated.
        checked = request(
            "inert", plan(world.arm_positions(), world.primary_joint() - 0.2)
        )
        fault_controls = {}
        for name, change in (
            ("stale", {"observed_ms": max(0, checked["now_ms"] - 201)}),
            ("unknown_item", {"item": "unknown"}),
            ("missing_coverage", {"coverage_known": False}),
        ):
            decision = judge_engine.decide({**checked, **change})
            if decision["allowed"]:
                raise AssertionError(name + " unexpectedly allowed")
            fault_controls[name] = decision
        safe = plan(world.arm_positions(), world.primary_joint() - 0.2)
        gate_controls = {
            "missing_binding": reject_at_gate(safe, "inert", expected="household:missing-binding", binding_override=None),
            "wrong_revision": reject_at_gate(safe, "inert", expected="household:binding-mismatch",
                binding_override=lambda value: {**value, "world_revision": value["world_revision"] + 100}),
            "wrong_item": reject_at_gate(safe, "inert", expected="household:binding-mismatch",
                binding_override=lambda value: {**value, "item_id": "unobserved-item"}),
        }
        completed = True
        return {
            "profile": "household_hazards",
            "ok": True,
            "hazard_checks": rows,
            "negative_controls": fault_controls,
            "mandatory_gate_controls": gate_controls,
            "semantic_input": "trusted_injected_fixtures",
            "geometry_input": "native_gazebo_pose_and_measured_joint_fk",
            "execution_scope": "root_policy_bound_mandatory_rust_household_gate",
            "continuous_semantic_monitoring": False,
            "fixture_pose_and_base_guard_during_execution": True,
            "geometry_scope": "end_effector_and_attached_item_sphere_only",
            "swept_radius_m": 0.15,
            "human_scope": "fixture_volume_not_person",
            "fall_scope": "end_effector_keepout_only",
            "push_or_drop_prevention": False,
            "chemical_scope": "fixture_bleach_ammonia_sequence",
            "clock_domain": "gazebo_sim_time+wall_monotonic",
            "plan_hash_scope": "audit_metadata_exact_signed_waypoints_rederived_at_rust_gate",
            "grasp_physics_validated": False,
        }
    except Exception:
        # Latch before teardown; never refresh world after a hazard failure.
        world.hazard_guard = lambda: False
        failure_at = time.monotonic()
        hold_observed = False
        try:
            wait_for(
                lambda: world.joint is not None and world.joint_received >= failure_at + 0.3
                and all(
                    abs(v) < 0.03
                    for name, v in zip(world.joint.name, world.joint.velocity)
                    if name in ("joint1", "joint2", "joint3", "joint4")
                ),
                2,
                processes,
                "fresh stopped joints before failed hazard teardown",
            )
            hold_observed = True
        except (TimeoutError, RuntimeError):
            pass
        target = native.sample(f"hazard_{active['case']}_target")
        (root / "hazard-diagnostics.json").write_text(
            json.dumps(
                {
                    "case": active["case"],
                    "failure_hold_observed": hold_observed,
                    "sim_ms": now(),
                    "sensor": {
                        k: world.sensor_info.get(k)
                        for k in (
                            "source",
                            "healthy",
                            "reason",
                            "stamp_ms",
                            "age_ms",
                            "returns",
                            "frames",
                            "classification",
                        )
                    },
                    "joint_age_wall_ms": (time.monotonic() - world.joint_received)
                    * 1000,
                    "odom_age_wall_ms": (time.monotonic() - world.odom_received) * 1000,
                    "target_age_wall_ms": (
                        (time.monotonic() - target[2]) * 1000 if target else None
                    ),
                    "target_stamp_ms": target[1] if target else None,
                    "native_error": native.failure,
                    "tracking_max_error_rad": tracking["max_error"],
                    "tracking_samples": tracking["samples"],
                    "motion_guard_active": motion_guard["active"],
                    "gate_state": (
                        {
                            k: world.states[-1][1].get(k)
                            for k in (
                                "mode",
                                "stop",
                                "armed",
                                "active",
                                "arm_cancelling",
                                "world_age_ms",
                                "arm_controller_ready",
                            )
                        }
                        if world.states
                        else None
                    ),
                    "recent_outcomes": [
                        {
                            "error": r.get("error"),
                            "decision": r.get("decision", {}).get("verdict"),
                        }
                        for _, r in [row for row in world.outcomes
                                     if row[1].get("decision") or row[1].get("error")][-5:]
                    ],
                },
                indent=2,
            )
        )
        raise
    finally:
        world.semantic_snapshot = None if completed else lambda _: None
        world.hazard_guard = None if completed else lambda: False
        if timer:
            world.destroy_timer(timer)
        if tracking_timer:
            world.destroy_timer(tracking_timer)
        attachment_stop.set()
        if attachment_thread:
            attachment_thread.join(timeout=3)
        native.close()
        if judge_engine:
            judge_engine.close()
