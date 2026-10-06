"""Causal OFF-only rearm barrier for the software action reference."""

import math


def accepted_world_ready(states, outcomes, started_at, now, previous_stamp=-1):
    """Discovery prerequisite only; this never arms a source or controller."""
    worlds = [(at, row.get("world_updated")) for at, row in outcomes
              if at >= started_at and isinstance(row.get("world_updated"), dict)]
    if not states or not worlds:
        return False
    world_at, updated = worlds[-1]
    state_at, state = states[-1]
    age = state.get("world_age_ms")
    return (type(updated.get("stamp_ms")) is int and updated["stamp_ms"] > previous_stamp
            and updated["stamp_ms"] >= 0
            and 0 <= now - world_at < .1 and world_at < state_at
            and 0 <= now - state_at < .1
            and state.get("mode") == "normal" and state.get("active") is None
            and not state.get("arm_cancelling") and state.get("recorder_ok") is True
            and state.get("state_ok") is True and type(age) is int and 0 <= age < 75)


def rearm_ready(states, outcomes, sent_at, proposal_id=None):
    decisions = [(at, value) for at, value in outcomes if at >= sent_at
                 and ("decision" in value or "rejected" in value)]
    if not states or not decisions:
        return False
    stop_at, outcome = decisions[-1]
    decision = outcome.get("decision") or {}
    state_at, state = states[-1]
    age = state.get("world_age_ms")
    return (decision.get("verdict") == "yun" and decision.get("action") == {"type": "stop"}
            and (proposal_id is None or decision.get("proposal_id") == proposal_id)
            and "rejected" not in outcome and state_at > stop_at
            and state.get("mode") == "normal" and "vla" in state.get("armed", [])
            and state.get("active") is None and not state.get("arm_cancelling")
            and state.get("arm_controller_ready") and state.get("recorder_ok")
            and state.get("state_ok") and type(age) is int and 0 <= age < 75)


def gazebo_rearm_ready(world, sent_at, now, arm_joints, nonces, proposal_id):
    """Fresh explicit fixture reset, immediately before a single arm dispatch."""
    if not rearm_ready(world.states, world.outcomes, sent_at, proposal_id):
        return False
    if not 0 <= now - world.states[-1][0] < 0.1:
        return False
    for target, samples in (("arm", world.guard_states), ("base", world.base_guard_states)):
        if not samples:
            return False
        received, guard = samples[-1]
        published = guard.get("published_wall_ns", 0) / 1e9
        if (not nonces.get(target) or guard.get("nonce") != nonces[target]
                or received < sent_at or published < sent_at
                or type(guard.get("holding")) is not bool
                or (target == "arm" and guard["holding"])
                or not 0 <= now - received < 0.1
                or not 0 <= now - published < 0.1):
            return False
    # This fixture dispatches only an arm goal. A locked wheel route is safe
    # provided its current report and measured physical stop are both fresh.
    arm = world.guard_states[-1][1]
    if arm.get("active_digest") != "0" * 64 or arm.get("goal_sequence") != 0:
        return False
    lease = arm.get("lease_received_wall_ns", 0) / 1e9
    if lease < sent_at or not 0 <= now - lease < 0.1:
        return False
    # Reset discards queued work at the controller update's cutoff. A goal
    # uses the conservatively backdated signed origin, so wait for an actual
    # idle lease from the owner issued strictly after that cutoff before
    # dispatching the single goal. This is not a sleep or a motion retry.
    issued = arm.get("lease_sent_ms")
    cutoff = arm.get("cutoff_ms")
    if type(issued) is not int or type(cutoff) is not int or not 0 <= cutoff < issued:
        return False
    if (world.joint is None or world.odom is None
            or not 0 <= now - world.joint_received < 0.1
            or not 0 <= now - world.odom_received < 0.1):
        return False
    twist = world.odom.twist.twist
    if any(not math.isfinite(value) or abs(value) >= 0.03
           for value in (twist.linear.x, twist.angular.z)):
        return False
    velocities = dict(zip(world.joint.name, world.joint.velocity))
    return all(name in velocities and math.isfinite(velocities[name])
               and abs(velocities[name]) < 0.03 for name in arm_joints)


def activated_guards_ready(world, reset_at, now, previous):
    """Both controller activations must have emitted fresh, rotated challenges."""
    for target, samples in (("arm", world.guard_states), ("base", world.base_guard_states)):
        if not samples:
            return False
        received, guard = samples[-1]
        nonce = guard.get("nonce")
        published = guard.get("published_wall_ns", 0) / 1e9
        if (not nonce or nonce == previous.get(target) or received < reset_at
                or published < reset_at or not 0 <= now - received < 0.1
                or not 0 <= now - published < 0.1):
            return False
    return True


def arm_fault_owner_ready(states, isolated):
    """Startup hint only; the explicit OFF/controller barrier grants motion."""
    if not states:
        return False
    state = states[-1][1]
    matched = state.get("signed_vla_writers_matched")
    return (state.get("mode") == "normal" and state.get("arm_controller_ready") is True
            and (not isolated or type(matched) is int and matched > 0))
