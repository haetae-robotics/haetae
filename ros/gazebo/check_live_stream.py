#!/usr/bin/env python3
"""Check that a live browser sees the running ROS/Gazebo reference events."""

import argparse
import json
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import urlopen


def run(port, output):
    address = f"http://127.0.0.1:{port}/events"
    deadline = time.monotonic() + 180
    while True:
        try:
            response = urlopen(address, timeout=10)
            break
        except URLError:
            if time.monotonic() >= deadline:
                raise TimeoutError("live server did not start")
            time.sleep(0.1)

    received = []
    with response:
        for line in response:
            if time.monotonic() >= deadline:
                raise TimeoutError("live stream did not finish")
            if not line.startswith(b"data: "):
                continue
            row = json.loads(line[6:])
            received.append(row)
            if row["kind"] == "result":
                break
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row) + "\n" for row in received))

    moving = any(row["kind"] == "telemetry" and abs(row["speed"]) > 0.05
                 for row in received)
    phases = [row["label"] for row in received if row["kind"] == "phase"]
    rejected_arm = any(row["kind"] == "decision" and
                       "envelope:arm-position" in row.get("fired", [])
                       for row in received)
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
    assert "사람 등장" in phases and "Gazebo 바퀴 정지" in phases
    assert rejected_arm, "no live arm denial"
    assert "Gazebo 팔 관절 정지" in phases
    assert result.get("kind") == "result" and result["result"]["ok"]
    print(f"live stream ok: {len(received)} messages", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.port, args.out)
