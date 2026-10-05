"""Causal OFF-only rearm barrier for the software action reference."""


def rearm_ready(states, outcomes, sent_at):
    decisions = [(at, value) for at, value in outcomes if at >= sent_at
                 and ("decision" in value or "rejected" in value)]
    if not states or not decisions:
        return False
    stop_at, outcome = decisions[-1]
    decision = outcome.get("decision") or {}
    state_at, state = states[-1]
    age = state.get("world_age_ms")
    return (decision.get("verdict") == "yun" and decision.get("action") == {"type": "stop"}
            and "rejected" not in outcome and state_at > stop_at
            and state.get("mode") == "normal" and "vla" in state.get("armed", [])
            and state.get("active") is None and not state.get("arm_cancelling")
            and state.get("arm_controller_ready") and state.get("recorder_ok")
            and state.get("state_ok") and type(age) is int and 0 <= age < 75)
