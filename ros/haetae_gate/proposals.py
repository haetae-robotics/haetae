"""Strict source proposal decoding shared by isolated and legacy transports."""
import math


class InvalidProposal(ValueError):
    pass


def base_action(msg, ttl):
    if any((msg.twist.linear.y, msg.twist.linear.z, msg.twist.angular.x, msg.twist.angular.y)):
        raise InvalidProposal("unsupported TwistStamped component")
    if not all(math.isfinite(v) for v in (msg.twist.linear.x, msg.twist.angular.z)):
        raise InvalidProposal("nonfinite TwistStamped component")
    linear, angular = msg.twist.linear.x, msg.twist.angular.z
    return {"type": "stop"} if linear == angular == 0.0 else {
        "type": "velocity", "linear": linear, "angular": angular, "ttl_ms": ttl}


def arm_action(msg, joints, fixed_ttl_ms=0):
    if type(fixed_ttl_ms) is not int or fixed_ttl_ms not in (0, 1000):
        raise InvalidProposal("unsupported fixed arm lease")
    if list(msg.joint_names) != joints:
        raise InvalidProposal("wrong arm joint names")
    if not msg.points:
        return {"type": "stop"}
    points = []
    for p in msg.points:
        if p.velocities or p.accelerations or p.effort:
            raise InvalidProposal("only positions are accepted")
        if len(p.positions) != len(joints) or not all(math.isfinite(v) for v in p.positions):
            raise InvalidProposal("invalid arm positions")
        if p.time_from_start.sec < 0 or p.time_from_start.nanosec >= 1_000_000_000:
            raise InvalidProposal("invalid arm time")
        millis = p.time_from_start.sec * 1000 + p.time_from_start.nanosec // 1_000_000
        points.append({"time_from_start_ms": millis, "positions": list(p.positions)})
    duration = points[-1]["time_from_start_ms"]
    if fixed_ttl_ms and duration > fixed_ttl_ms:
        raise InvalidProposal("arm plan exceeds fixed lease")
    return {"type": "joint_trajectory", "points": points,
            "ttl_ms": fixed_ttl_ms or duration}
