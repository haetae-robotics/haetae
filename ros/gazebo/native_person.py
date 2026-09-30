"""Kinematic adult geometry in Gazebo; only the test orchestrator drives it.

The security perception path never reads this desired pose. Parts have both
visuals and collisions, and gait follows simulation time / planted-foot IK.
"""

import math
import multiprocessing
import threading
import time
from xml.etree import ElementTree as E

PARTS = [("torso", "cylinder", (0.145, 0.65)), ("head", "sphere", (0.095,))]
for side in (-1, 1):
    for part, radius, length in [
        ("thigh", 0.045, 0.43),
        ("shin", 0.038, 0.42),
        ("upper_arm", 0.035, 0.25),
        ("forearm", 0.03, 0.25),
    ]:
        PARTS.append((part + str(side), "cylinder", (radius, length)))
    PARTS.append(("foot" + str(side), "box", (0.16, 0.09, 0.055)))


def add_native_scene(world):
    E.SubElement(
        world,
        "plugin",
        filename="gz-sim-sensors-system",
        name="gz::sim::systems::Sensors",
    ).append(E.Element("render_engine"))
    world[-1][0].text = "ogre2"
    for name, kind, dimensions in PARTS:
        model = E.SubElement(world, "model", name="person_" + name)
        E.SubElement(model, "static").text = "true"
        E.SubElement(model, "pose").text = "0 0 -5 0 0 0"
        link = E.SubElement(model, "link", name="body")
        for child in ("visual", "collision"):
            row = E.SubElement(link, child, name=child)
            geometry = E.SubElement(row, "geometry")
            shape = E.SubElement(geometry, kind)
            if kind == "box":
                E.SubElement(shape, "size").text = " ".join(map(str, dimensions))
            else:
                E.SubElement(shape, "radius").text = str(dimensions[0])
                if kind == "cylinder":
                    E.SubElement(shape, "length").text = str(dimensions[1])
            if child == "visual":
                material = E.SubElement(row, "material")
                color = (
                    "0.17 0.32 0.43 1"
                    if name == "torso" or "arm" in name
                    else "0.76 0.62 0.47 1" if name == "head" else "0.12 0.15 0.19 1"
                )
                for tag in ("ambient", "diffuse"):
                    E.SubElement(material, tag).text = color
    # Known target proves the renderer and scan coverage are alive even when
    # the controlled bay is empty. Losing it invalidates the entire sample.
    marker = E.SubElement(world, "model", name="lidar_calibration")
    E.SubElement(marker, "static").text = "true"
    E.SubElement(marker, "pose").text = "3 7 1.16 0 0 0"
    link = E.SubElement(marker, "link", name="target")
    visual = E.SubElement(link, "visual", name="target")
    geom = E.SubElement(visual, "geometry")
    cylinder = E.SubElement(geom, "cylinder")
    E.SubElement(cylinder, "radius").text = ".08"
    E.SubElement(cylinder, "length").text = ".5"
    mat = E.SubElement(visual, "material")
    for tag in ("ambient", "diffuse"):
        E.SubElement(mat, tag).text = "0.15 0.68 0.69 1"
    scanner = E.SubElement(world, "model", name="bay_lidar")
    E.SubElement(scanner, "static").text = "true"
    E.SubElement(scanner, "pose").text = "0 0 0 0 0 0"
    link = E.SubElement(scanner, "link", name="sensor")
    sensor = E.SubElement(link, "sensor", name="bay_scan", type="gpu_lidar")
    E.SubElement(sensor, "pose").text = "3 5 1.16 0 0 0"
    for tag, value in [
        ("topic", "/haetae/sensors/bay_scan"),
        ("update_rate", "30"),
        ("always_on", "true"),
        ("visualize", "false"),
    ]:
        E.SubElement(sensor, tag).text = value
    ray = E.SubElement(sensor, "ray")
    scan = E.SubElement(ray, "scan")
    for tag, rows in [
        (
            "horizontal",
            [
                ("samples", "720"),
                ("resolution", "1"),
                ("min_angle", str(-math.pi)),
                ("max_angle", str(math.pi)),
            ],
        ),
        (
            "vertical",
            [
                ("samples", "1"),
                ("resolution", "1"),
                ("min_angle", "0"),
                ("max_angle", "0"),
            ],
        ),
    ]:
        row = E.SubElement(scan, tag)
        for key, value in rows:
            E.SubElement(row, key).text = value
    ranges = E.SubElement(ray, "range")
    for tag, value in [("min", ".1"), ("max", "10"), ("resolution", ".001")]:
        E.SubElement(ranges, tag).text = value


def geometry_poses(position, motion):
    if position is None:
        return {name: ((0, 0, -5), (0, 0, 0, 1)) for name, _, _ in PARTS}
    x, y = position
    heading = (motion or {}).get("heading", 0)
    distance = (motion or {}).get("distance_m", 0)
    c, s = math.cos(heading), math.sin(heading)

    def transform(p):
        forward, left, z = p
        return (x + c * forward - s * left, y + s * forward + c * left, z)

    result = {}

    def segment(name, a, b):
        a, b = transform(a), transform(b)
        v = [b[i] - a[i] for i in range(3)]
        length = math.sqrt(sum(k * k for k in v))
        v = [k / length for k in v]
        # Quaternion rotating cylinder +Z to the segment direction.
        if v[2] < -0.999999:
            q = (1, 0, 0, 0)
        else:
            q = (-v[1], v[0], 0, 1 + v[2])
            n = math.sqrt(sum(k * k for k in q))
            q = tuple(k / n for k in q)
        result[name] = (tuple((a[i] + b[i]) / 2 for i in range(3)), q)

    qyaw = (0, 0, math.sin(heading / 2), math.cos(heading / 2))
    result["torso"] = (transform((0, 0, 1.16)), qyaw)
    result["head"] = (transform((0, 0, 1.625)), qyaw)
    for i, side in enumerate((-1, 1)):
        cycle = (distance / 0.36 + i * 0.5) % 1
        stance = cycle < 0.5
        swing = (cycle - 0.5) * 2
        forward = (
            0.09 - 0.36 * cycle
            if stance
            else -0.09 + 0.18 * swing * swing * (3 - 2 * swing)
        )
        ankle_z = 0.05 + (0 if stance else math.sin(math.pi * swing) * 0.065)
        down = 0.89 - ankle_z
        reach = min(0.85 - 1e-6, math.hypot(down, forward))
        alpha = math.atan2(forward, down) + math.acos(
            max(-1, min(1, (0.43**2 + reach**2 - 0.42**2) / (2 * 0.43 * reach)))
        )
        hip = (0, side * 0.075, 0.89)
        knee = (0.43 * math.sin(alpha), side * 0.075, 0.89 - 0.43 * math.cos(alpha))
        ankle = (forward, side * 0.075, ankle_z)
        segment("thigh" + str(side), hip, knee)
        segment("shin" + str(side), knee, ankle)
        result["foot" + str(side)] = (
            transform((forward + 0.025, side * 0.075, ankle_z - 0.0225)),
            qyaw,
        )
        angle = math.cos(distance / 0.36 * math.pi * 2 + i * math.pi) * 0.16
        shoulder = (0, side * 0.205, 1.425)
        elbow = (math.sin(angle) * 0.25, side * 0.205, 1.425 - math.cos(angle) * 0.25)
        hand = (
            elbow[0] + math.sin(angle + 0.12) * 0.25,
            side * 0.205,
            elbow[2] - math.cos(angle + 0.12) * 0.25,
        )
        segment("upper_arm" + str(side), shoulder, elbow)
        segment("forearm" + str(side), elbow, hand)
    return result


def _set_poses(node, poses):
    from gz.msgs10.pose_v_pb2 import Pose_V
    from gz.msgs10.boolean_pb2 import Boolean

    request = Pose_V()
    for name, (position, orientation) in poses.items():
        p = request.pose.add()
        p.name = name
        p.position.x, p.position.y, p.position.z = position
        p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w = orientation
    ok, response = node.request(
        "/world/empty/set_pose_vector", request, Pose_V, Boolean, 100
    )
    return ok and response.data


def _drive_native(target, calibration_ack, status, stop):
    """Keep blocking native transport calls outside the ROS/sensor process.

    A Python thread is insufficient: the transport binding can retain the GIL
    while waiting for a service response. Shared memory holds only the latest
    desired geometry, so a slow response cannot build a stale command queue.
    """
    from gz.transport13 import Node

    node = Node()
    previous = None
    while not stop.is_set():
        with target.get_lock():
            present, x, y, heading, distance, visible, generation = target[:]
        try:
            if generation != calibration_ack.value:
                poses = {
                    "lidar_calibration": ((3, 7, 1.16 if visible else -5), (0, 0, 0, 1))
                }
                if _set_poses(node, poses):
                    calibration_ack.value = int(generation)
            position = (x, y) if present else None
            poses = {
                "person_" + name: value
                for name, value in geometry_poses(
                    position, {"heading": heading, "distance_m": distance}
                ).items()
            }
            if poses != previous:
                if _set_poses(node, poses):
                    previous = poses
                    status.value = 1
                else:
                    status.value = 0
        except Exception:
            status.value = 0
        stop.wait(0.005)


class NativeScene:
    def __init__(self, desired, perception):
        from gz.transport13 import Node
        from gz.msgs10.laserscan_pb2 import LaserScan
        from gz.msgs10.pose_v_pb2 import Pose_V

        self.node = Node()
        self.desired = desired
        self.running = True
        self.lock = threading.Lock()
        self.observed = None
        self.observed_stamp_ms = None
        self.node.subscribe(LaserScan, "/haetae/sensors/bay_scan", perception.receive)
        self.node.subscribe(Pose_V, "/world/empty/pose/info", self.observe)
        # Never fork an initialized ROS / Gazebo transport process.
        context = multiprocessing.get_context("spawn")
        self.target = context.Array("d", [0, 0, 0, 0, 0, 1, 0])
        self.calibration_ack = context.Value("q", 0)
        self.status = context.Value("i", -1)
        self.stop = context.Event()
        self.process = context.Process(
            target=_drive_native,
            args=(self.target, self.calibration_ack, self.status, self.stop),
            daemon=True,
        )
        self.process.start()
        self.thread = threading.Thread(target=self.drive, daemon=True)
        self.thread.start()

    @property
    def error(self):
        if not self.process.is_alive():
            return "native pose driver exited"
        if self.status.value == 0:
            return "native person pose command not acknowledged"
        return None

    def observe(self, msg):
        for p in msg.pose:
            if p.name == "person_torso":
                with self.lock:
                    self.observed = (p.position.x, p.position.y, p.position.z)
                    self.observed_stamp_ms = (
                        msg.header.stamp.sec * 1000 + msg.header.stamp.nsec // 1_000_000
                    )

    def observed_sample(self):
        with self.lock:
            return self.observed, self.observed_stamp_ms

    def drive(self):
        while self.running:
            position, motion = self.desired()
            motion = motion or {}
            with self.target.get_lock():
                self.target[:5] = [
                    int(position is not None),
                    *(position or (0, 0)),
                    motion.get("heading", 0),
                    motion.get("distance_m", 0),
                ]
            time.sleep(0.005)

    def calibration_visible(self, visible):
        with self.target.get_lock():
            self.target[5] = int(visible)
            self.target[6] += 1
            generation = int(self.target[6])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.process.is_alive():
            if self.calibration_ack.value == generation:
                return True
            time.sleep(0.01)
        return False

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.stop.set()
        self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
