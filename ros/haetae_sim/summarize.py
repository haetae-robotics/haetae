#!/usr/bin/env python3
"""Summarize an immutable 8 x 5 reference-simulator evidence directory."""

import json
from pathlib import Path
import sys


def main(root):
    rows = [json.loads(path.read_text()) for path in Path(root).glob("*/result.json")]
    pairs = {(row["scenario"], row["repeat"]) for row in rows}
    expected = {(scenario, repeat) for scenario in range(1, 9) for repeat in range(1, 6)}
    if pairs != expected or len(rows) != 40 or not all(row["ok"] for row in rows):
        raise SystemExit("incomplete or failed reference scenario matrix")
    if not all(row["sillok"] and all(
        log.get("fully_sealed", False) or
        (row["scenario"] == 1 and log.get("present") is False)
        for log in row["sillok"]
    ) for row in rows):
        raise SystemExit("reference scenario has an unsealed log")
    print("### Haetae ROS 2 reference scenarios")
    print()
    print("All 8 scenarios passed 5 repeats in the hosted Jazzy reference model. "
          "These measurements do not describe a physical robot.")
    print()
    print("| Scenario | Runs | Worst gate zero | Smallest gap | Other observation |")
    print("|---|---:|---:|---:|---|")
    for scenario in range(1, 9):
        group = [row for row in rows if row["scenario"] == scenario]
        zeros = [row["metrics"]["gate_zero_ms"] for row in group
                 if "gate_zero_ms" in row["metrics"]]
        gaps = [row["metrics"]["minimum_gap_m"] for row in group
                if "minimum_gap_m" in row["metrics"]]
        other = ""
        if scenario == 2:
            other = f"max command {max(row['metrics']['max_command'] for row in group):.2f} m/s"
        elif scenario == 7:
            other = (f"deadman {max(row['metrics']['node_kill_deadman_ms'] for row in group):.1f} ms; "
                     f"child kill zero {max(row['metrics']['child_kill_zero_ms'] for row in group):.1f} ms")
        elif scenario == 8:
            other = "estop persisted after restart"
        print(f"| {scenario} | {len(group)} | "
              f"{max(zeros):.1f} ms" if zeros else f"| {scenario} | {len(group)} | —", end="")
        print(f" | {min(gaps):.3f} m" if gaps else " | —", end="")
        print(f" | {other} |")


if __name__ == "__main__":
    main(sys.argv[1])
