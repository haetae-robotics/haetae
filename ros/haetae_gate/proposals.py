"""Strict source proposal decoding shared by isolated and legacy transports."""
import math
import json

SEMANTIC_PREFIX = "haetae.semantic.v1:"
SEMANTIC_FIELDS = {"schema_version", "world_revision", "task_revision", "task_id",
                   "step_id", "robot_id", "model_sha256", "tool_id", "item_id"}


class InvalidProposal(ValueError):
    pass


def semantic_binding(frame_id):
    """Decode untrusted references, never world facts or a safety verdict.

    Legacy frame names carry no semantic authority. Household policy decides
    whether missing references are acceptable at the authenticated Rust gate.
    """
    if not isinstance(frame_id, str) or not frame_id.startswith(SEMANTIC_PREFIX):
        return None
    if len(frame_id) > 2048:
        raise InvalidProposal("semantic reference exceeds bounds")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidProposal("duplicate semantic reference field")
            result[key] = value
        return result

    try:
        value = json.loads(frame_id[len(SEMANTIC_PREFIX):], object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise InvalidProposal("malformed semantic reference") from exc
    if not isinstance(value, dict) or set(value) != SEMANTIC_FIELDS:
        raise InvalidProposal("unexpected semantic reference fields")
    for key in ("schema_version", "world_revision", "task_revision"):
        if type(value[key]) is not int or not 0 < value[key] <= 2**64 - 1:
            raise InvalidProposal("invalid semantic revision")
    if value["schema_version"] != 1:
        raise InvalidProposal("unsupported semantic schema")
    for key in SEMANTIC_FIELDS - {"schema_version", "world_revision", "task_revision"}:
        text = value[key]
        if (not isinstance(text, str) or not 1 <= len(text) <= 64
                or not text.isascii() or any(not (c.isalnum() or c in "_.-") for c in text)):
            raise InvalidProposal("invalid semantic identity")
    digest = value["model_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise InvalidProposal("invalid semantic model hash")
    return value


def semantic_frame(binding):
    frame = SEMANTIC_PREFIX + json.dumps(binding, sort_keys=True, separators=(",", ":"))
    semantic_binding(frame)
    return frame


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
