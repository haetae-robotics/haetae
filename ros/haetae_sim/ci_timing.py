"""Shared-runner observations; functional assertions stay in each scenario."""
import math


def timing_measurement(label, elapsed_ms, limit_ms):
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in (elapsed_ms, limit_ms)):
        raise ValueError("timing observations require finite nonnegative milliseconds")
    within = elapsed_ms <= limit_ms
    if not within:
        print(f"::warning::{label}: {elapsed_ms:.1f} ms exceeds {limit_ms:g} ms "
              "(shared-runner measurement only)", flush=True)
    return {"elapsed_ms": elapsed_ms, "limit_ms": limit_ms, "within_limit": within, "blocking": False}
