"""Small deterministic differential-base model used by the ROS test harness.

This models controller acceleration and the command deadman, not a real robot's
motor driver, traction, or certified stop circuit.
"""

from dataclasses import dataclass
import math
from typing import Optional


@dataclass
class Base:
    x: float = 5.0
    y: float = 5.0
    yaw: float = 0.0
    speed: float = 0.0
    target: float = 0.0
    angular: float = 0.0
    last_command: Optional[float] = None
    max_accel: float = 2.0
    max_decel: float = 1.0
    deadman_s: float = 0.25

    def command(self, linear: float, angular: float, now: float) -> None:
        if not math.isfinite(linear) or not math.isfinite(angular):
            raise ValueError("nonfinite controller command")
        self.target = linear
        self.angular = angular
        self.last_command = now

    def advance(self, dt: float, now: float) -> None:
        if dt < 0 or dt > 0.1:
            raise ValueError("simulation step must be between 0 and 100 ms")
        leased = self.last_command is not None and 0 <= now - self.last_command < self.deadman_s
        target = self.target if leased else 0.0
        if target == 0.0 or self.speed * target < 0 or abs(target) < abs(self.speed):
            rate = self.max_decel
        else:
            rate = self.max_accel
        delta = max(-rate * dt, min(rate * dt, target - self.speed))
        self.speed += delta
        if abs(self.speed) < 1e-9:
            self.speed = 0.0
        self.yaw += self.angular * dt if leased else 0.0
        self.x += self.speed * math.cos(self.yaw) * dt
        self.y += self.speed * math.sin(self.yaw) * dt
