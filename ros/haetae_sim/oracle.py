"""Independent measurement of simulated base distance and stop latency."""

import math


def disc_gap(robot_x, robot_y, robot_radius, human_x, human_y, human_radius):
    return math.hypot(robot_x - human_x, robot_y - human_y) - robot_radius - human_radius


def disc_hits_rect(x, y, radius, low_x, low_y, high_x, high_y):
    nearest_x = min(max(x, low_x), high_x)
    nearest_y = min(max(y, low_y), high_y)
    return math.hypot(x - nearest_x, y - nearest_y) <= radius


def stop_latency_ms(trace, trigger_s):
    for row in trace:
        if row["time_s"] >= trigger_s and row["speed"] == 0.0:
            return round((row["time_s"] - trigger_s) * 1000, 1)
    return None
