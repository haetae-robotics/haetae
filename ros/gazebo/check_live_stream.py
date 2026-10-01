#!/usr/bin/env python3
"""Check that a live browser sees the running ROS/Gazebo reference events."""

import argparse
import json
import math
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import urlopen


def run(port, output, timeout_seconds=180):
    address = f"http://127.0.0.1:{port}/events"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            response = urlopen(address, timeout=10)
            break
        except (URLError, ConnectionError):
            if time.monotonic() >= deadline:
                raise TimeoutError("live server did not start")
            time.sleep(0.1)

    received = []
    try:
        with response:
            for line in response:
                if time.monotonic() >= deadline:
                    raise TimeoutError("live stream did not finish")
                if not line.startswith(b"data: "):
                    continue
                row = json.loads(line[6:])
                received.append(row)
                if row["kind"] in ("result", "error"):
                    break
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(row) + "\n" for row in received))
    if received and received[-1]["kind"] == "error":
        raise RuntimeError("live runner failed: " + received[-1].get("detail", "unknown error"))

    moving = any(row["kind"] == "telemetry" and abs(row["speed"]) > 0.05
                 for row in received)
    phases = [row["label"] for row in received if row["kind"] == "phase"]
    rejected_arm = any(row["kind"] == "decision" and
                       "envelope:arm-position" in row.get("fired", [])
                       for row in received)
    # Compare measured person geometry against its own Gazebo source stamp,
    # rather than an unrelated joint-state stamp from another ROS callback.
    previous_person = None
    for row in received:
        if row["kind"] != "telemetry":
            continue
        if not row.get("humans"):
            previous_person = None
            continue
        point = row["humans"][0]["pos"]
        stamp = row.get("native_person_stamp_ms")
        assert isinstance(stamp, int) and stamp > 0, "missing native pose timestamp"
        if previous_person:
            old, old_stamp = previous_person
            delta = math.hypot(point["x"] - old["x"], point["y"] - old["y"])
            limit = .35 * max(0, stamp - old_stamp) / 1000 + .002
            assert delta <= limit, f"native person jumped: {delta} m > {limit} m"
        previous_person = (point, stamp)
    positive_command = False
    zero_after_positive = False
    for row in received:
        if row["kind"] == "base_command":
            if abs(row["linear"]) > 0.01:
                positive_command = True
            elif positive_command:
                zero_after_positive = True
    result = received[-1] if received else {}
    assert moving, "no live Gazebo motion telemetry"
    assert zero_after_positive, "no live ROS zero command after motion"
    assert any(phase in phases for phase in ("사람 등장", "사람이 걸어 접근합니다"))
    assert "Gazebo 바퀴 정지" in phases
    assert rejected_arm, "no live arm denial"
    assert "Gazebo 팔 관절 정지" in phases
    assert result.get("kind") == "result" and result["result"]["ok"]
    print(f"live stream ok: {len(received)} messages", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180,
                        help="total setup/scenario wall timeout; does not alter actuation budgets")
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 900:
        parser.error("--timeout-seconds must be finite and in (0, 900]")
    run(args.port, args.out, args.timeout_seconds)
