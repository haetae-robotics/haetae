"""Conservative occupancy from a real Gazebo GPU lidar in the controlled bay.

This single scan plane is not a human classifier. Every uncalibrated return
inside the bay is treated as a person for the existing restrictive policy.
Unknown/stale/invalid coverage cannot become an empty, confident world.
"""

from dataclasses import dataclass
import math
import threading
import time

SCAN_TOPIC = "/haetae/sensors/bay_scan"
SCANNER = (3.0, 5.0, 1.16)
CALIBRATION = (3.0, 7.0)
BAY = (1.0, 10.0, 1.0, 10.0)
COVERAGE_MARGIN_M = 1.5  # Footprint + proximity + maximum policy braking horizon.
COUNT = 720
MAX_AGE_MS = 200


@dataclass(frozen=True)
class Frame:
    stamp_ms: int
    received: float
    points: tuple
    healthy: bool
    reason: str


def decode_scan(msg, received):
    stamp = msg.header.stamp.sec * 1000 + msg.header.stamp.nsec // 1_000_000

    def bad(reason):
        return Frame(stamp, received, (), False, reason)

    if (
        msg.frame != "bay_lidar::sensor::bay_scan"
        or stamp <= 0
        or msg.count != COUNT
        or msg.vertical_count != 1
        or len(msg.ranges) != COUNT
    ):
        return bad("invalid scan shape or timestamp")
    expected = 2 * math.pi / (COUNT - 1)
    scalars = (
        msg.angle_min,
        msg.angle_max,
        msg.angle_step,
        msg.range_min,
        msg.range_max,
        msg.vertical_angle_min,
        msg.vertical_angle_max,
    )
    if not all(math.isfinite(v) for v in scalars) or not (
        abs(msg.angle_min + math.pi) < 1e-5
        and abs(msg.angle_max - math.pi) < 1e-5
        and abs(msg.angle_step - expected) < 1e-6
        and abs(msg.range_min - 0.1) < 1e-5
        and abs(msg.range_max - 10.0) < 1e-5
        and abs(msg.vertical_angle_min) < 1e-5
        and abs(msg.vertical_angle_max) < 1e-5
    ):
        return bad("unexpected scan calibration")
    pose = msg.world_pose
    if (
        not all(
            math.isfinite(v)
            for v in (
                pose.position.x,
                pose.position.y,
                pose.position.z,
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            )
        )
        or max(
            abs(a - b)
            for a, b in zip(
                (pose.position.x, pose.position.y, pose.position.z), SCANNER
            )
        )
        > 0.01
        or max(
            abs(pose.orientation.x),
            abs(pose.orientation.y),
            abs(pose.orientation.z),
            abs(pose.orientation.w - 1.0),
        )
        > 0.001
    ):
        return bad("unexpected sensor pose")
    points, marker = [], False
    for i, distance in enumerate(msg.ranges):
        if math.isinf(distance) and distance > 0:
            continue  # Gazebo's explicit no-return value, not missing data.
        if (
            not math.isfinite(distance)
            or not msg.range_min <= distance <= msg.range_max
        ):
            return bad("invalid range")
        angle = msg.angle_min + i * msg.angle_step
        x, y = SCANNER[0] + distance * math.cos(angle), SCANNER[
            1
        ] + distance * math.sin(angle)
        if math.hypot(x - CALIBRATION[0], y - CALIBRATION[1]) < 0.12:
            marker = True
        elif BAY[0] <= x <= BAY[1] and BAY[2] <= y <= BAY[3]:
            points.append((x, y))
    return Frame(
        stamp,
        received,
        tuple(points),
        marker,
        "ok" if marker else "calibration target missing",
    )


class Perception:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.drop_frames = False  # Test-only simulated receiver disconnect.
        self.frames = 0

    def receive(self, msg):
        with self.lock:
            if self.drop_frames:
                return
            frame = decode_scan(msg, time.monotonic())
            # Replayed/out-of-order scans must not refresh a stale sample.
            if self.frame and frame.stamp_ms <= self.frame.stamp_ms:
                return
            self.frame = frame
            self.frames += 1

    def snapshot(self, now_ms, robot):
        with self.lock:
            frame = self.frame
        reason = "no sensor frame"
        healthy = False
        points = ()
        age = None
        if frame:
            age = now_ms - frame.stamp_ms
            reason = frame.reason
            healthy = (
                frame.healthy
                and 0 <= age < MAX_AGE_MS
                and 0 <= time.monotonic() - frame.received < MAX_AGE_MS / 1000
            )
            if frame.healthy and not healthy:
                reason = "sensor frame stale or clock inconsistent"
            points = frame.points if healthy else ()
        if not (
            BAY[0] + COVERAGE_MARGIN_M <= robot[0] <= BAY[1] - COVERAGE_MARGIN_M
            and BAY[2] + COVERAGE_MARGIN_M <= robot[1] <= BAY[3] - COVERAGE_MARGIN_M
        ):
            healthy, reason, points = False, "robot outside calibrated bay", ()
        # Keep every return conservative: the nearest point per connected
        # cluster is a surface distance, never an optimistic estimated centre.
        clusters = []
        for point in points:
            if not clusters or math.dist(clusters[-1][-1], point) > 0.16:
                clusters.append([])
            clusters[-1].append(point)
        humans = []
        for i, cluster in enumerate(clusters):
            x, y = min(cluster, key=lambda p: math.dist(p, robot))
            humans.append(
                {
                    "id": "lidar-obstacle-" + str(i),
                    "class": "adult",
                    "pos": {"x": x, "y": y},
                }
            )
        info = {
            "source": "gazebo_gpu_lidar",
            "healthy": healthy,
            "reason": reason,
            "stamp_ms": frame.stamp_ms if frame else None,
            "age_ms": age,
            "returns": len(points),
            "frames": self.frames,
            "classification": "conservative_occupancy",
            "bay": list(BAY),
        }
        return humans, 1.0 if healthy else 0.0, info
