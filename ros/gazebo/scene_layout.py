"""Place native test person paths beside the robot, in its local frame.

The test person remains inside the product gate's configured proximity boundary,
but the displayed silhouette stands clear of the base and the arm sweep.
"""

import math
from dataclasses import dataclass

PERSON_FORWARD_M = 0.40
PERSON_LEFT_M = 0.78
PERSON_BODY_RADIUS_M = 0.29  # Adult silhouette, including the walking gait.
PERSON_ENTRY_LEFT_M = 1.90
PERSON_WALK_SPEED_MPS = 0.24


def nearby_person(x, y, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return (x + PERSON_FORWARD_M * c - PERSON_LEFT_M * s,
            y + PERSON_FORWARD_M * s + PERSON_LEFT_M * c)


def person_entry(x, y, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return (x + PERSON_FORWARD_M * c - PERSON_ENTRY_LEFT_M * s,
            y + PERSON_FORWARD_M * s + PERSON_ENTRY_LEFT_M * c)


@dataclass(frozen=True)
class PersonWalk:
    """A scripted test person path sampled on the Gazebo clock.

    This path drives native geometry only. Security occupancy is obtained
    independently from the Gazebo lidar. It does not populate world inputs.
    """
    start: tuple
    end: tuple
    started_ms: int
    speed_mps: float = PERSON_WALK_SPEED_MPS
    distance_offset_m: float = 0.0

    def __post_init__(self):
        if self.speed_mps <= 0 or not all(math.isfinite(v) for v in (*self.start, *self.end, self.speed_mps)):
            raise ValueError("person path requires finite coordinates and positive speed")

    def sample(self, stamp_ms):
        dx, dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        length = math.hypot(dx, dy)
        travelled = min(length, max(0, stamp_ms - self.started_ms) / 1000 * self.speed_mps)
        fraction = travelled / length if length else 1.0
        position = (self.start[0] + dx * fraction, self.start[1] + dy * fraction)
        moving = travelled < length
        return position, {"heading": math.atan2(dy, dx),
                          "distance_m": self.distance_offset_m + travelled,
                          "speed_mps": self.speed_mps if moving else 0.0,
                          "moving": moving}
