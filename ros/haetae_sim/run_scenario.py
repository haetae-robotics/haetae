#!/usr/bin/env python3
"""Run one signed ROS 2 gate scenario against the reference base controller.

The resulting traces measure this simulator and CI host. They are not evidence
about a particular robot's motor stop, arm controller, or SROS2 permissions.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import rclpy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from oracle import stop_latency_ms
from sim_node import SimNode


def public(seed):
    return Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw).hex()


def fixture(root, binary, arm=False):
    policy = {
        "allowed_sources": ["vla"],
        "freshness": {"world_max_age_ms": 200, "proposal_max_age_ms": 1000,
                      "future_tolerance_ms": 20},
        "envelope": {"max_speed": 1.0, "workspace": {
            "min": {"x": 0, "y": 0}, "max": {"x": 10, "y": 10}}},
        "base": {"footprint_radius": 0.25, "max_decel": 1.0,
                 "max_angular": 1.0, "latency_ms": 50, "max_ttl_ms": 200},
        "zones": [{"id": "child-room", "area": {"min": {"x": 7, "y": 4},
                                                 "max": {"x": 8, "y": 6}}, "no_entry": True}],
        "rules": [{"id": "person", "when": {"human_within": {"distance": 0.3}},
                   "then": "bul"}],
    }
    if arm:
        policy["arm"] = {"joints": [{"name": "shoulder", "min_position": -1.0,
                                    "max_position": 1.0, "max_velocity": 2.0,
                                    "max_acceleration": 40.0}],
                         "max_points": 8, "max_duration_ms": 1000,
                         "max_start_error": 0.01, "max_tracking_error": 0.05,
                         "min_confidence": 0.9}
    policy_bytes = json.dumps(policy, separators=(",", ":")).encode()
    (root / "policy.json").write_bytes(policy_bytes)
    for name, seed in (("world", 2), ("fault", 3), ("vla", 4), ("log", 9)):
        path = root / (name + ".key")
        path.write_text((bytes([seed]) * 32).hex())
        path.chmod(0o600)
    body = json.dumps({"v": 1, "audience": "ros-sim", "epoch": 1,
                       "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
                       "keys": {"world": public(2), "fault": public(3), "vla": public(4)}},
                      separators=(",", ":"))
    signature = Ed25519PrivateKey.from_private_bytes(bytes([1]) * 32).sign(
        b"haetae-trust-bundle-v1\0" + body.encode()).hex()
    (root / "trust.json").write_text(json.dumps({"body": body, "signature": signature}))
    state = root / "state.json"
    subprocess.run([binary, "state", "set", "--state", str(state), "--mode", "normal",
                    "--by", "ci", "--reason", "scenario start"], check=True)
    params = {"haetae_gate": {"ros__parameters": {
        "haetae_bin": binary, "policy_path": str(root / "policy.json"),
        "state_path": str(state), "sillok_path": str(root / "sillok.jsonl"),
        "key_path": str(root / "log.key"), "trust_path": str(root / "trust.json"),
        "root_pubkey": public(1),
        "keys_json": json.dumps({name: str(root / (name + ".key"))
                                 for name in ("world", "fault", "vla")}),
        "inputs_json": json.dumps([{"topic": "/vla/cmd_vel", "source": "vla", "ttl_ms": 200}]),
        "arm_inputs_json": json.dumps([{"topic": "/vla/arm", "source": "vla"}]) if arm else "[]",
        "response_timeout_ms": 150,
    }}}
    (root / "params.yaml").write_text(json.dumps(params))


class Harness:
    def __init__(self, root, binary, scenario):
        self.root = root
        self.binary = binary
        self.scenario = scenario
        self.node = SimNode(start_x=5.0)
        self.gate = None
        self.stderr = None
        self.stream = None
        self.last_proposal = 0.0
        self.events = {}
        self.metrics = {}

    def start(self):
        repo = Path(__file__).resolve().parents[2]
        self.stderr = (self.root / "gate.stderr").open("ab")
        self.gate = subprocess.Popen([sys.executable, str(repo / "ros/haetae_gate/node.py"),
                                      "--ros-args", "--params-file", str(self.root / "params.yaml")],
                                     stderr=self.stderr)

    def stop(self, force=False):
        children = []
        if self.gate and self.gate.poll() is None:
            child_file = Path(f"/proc/{self.gate.pid}/task/{self.gate.pid}/children")
            if child_file.exists():
                children = [int(pid) for pid in child_file.read_text().split()]
        if self.gate and self.gate.poll() is None:
            if force:
                self.gate.kill()
            else:
                self.gate.terminate()
            try:
                self.gate.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.gate.kill()
                self.gate.wait()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            living = []
            for pid in children:
                stat = Path(f"/proc/{pid}/stat")
                if stat.exists() and stat.read_text().split()[2] != "Z":
                    living.append(pid)
            if not living:
                break
            time.sleep(0.01)
        if living:
            raise AssertionError("orphaned Rust gate did not exit after bridge death")
        if self.stderr:
            self.stderr.close()
            self.stderr = None

    def pump(self, expected_exit=False):
        now = time.monotonic()
        if self.gate and self.gate.poll() is not None and not expected_exit:
            raise AssertionError("gate exited: " + (self.root / "gate.stderr").read_text())
        if self.stream is not None and now - self.last_proposal >= 0.05:
            self.node.propose(self.stream)
            self.last_proposal = now
        rclpy.spin_once(self.node, timeout_sec=0.01)

    def until(self, predicate, timeout=5.0, expected_exit=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(expected_exit=expected_exit)
            if predicate():
                return time.monotonic()
        raise AssertionError(json.dumps({
            "last_state": self.node.states[-1][1] if self.node.states else None,
            "last_outcome": self.node.outcomes[-1][1] if self.node.outcomes else None,
            "x": self.node.base.x,
            "recent_commands": self.node.commands[-5:],
            "gate_stderr": (self.root / "gate.stderr").read_text(),
        }, default=str))

    def command_since(self, at, predicate):
        return next((t for t, value in self.node.commands if t >= at and predicate(value)), None)

    def ready(self):
        self.until(lambda: self.node.states and self.node.states[-1][1]["mode"] == "normal"
                   and self.node.commands)
        self.stream = 0.0
        self.until(lambda: self.node.states and "vla" in self.node.states[-1][1]["armed"])

    def move(self, speed):
        start = time.monotonic()
        self.stream = speed
        self.last_proposal = 0.0
        self.until(lambda: self.command_since(start, lambda v: v > 0.1) is not None)
        return self.command_since(start, lambda v: v > 0.1)

    def run(self):
        self.start()
        self.ready()
        number = self.scenario
        if number == 1:
            at = self.move(0.5)
            self.until(lambda: self.node.base.x >= 5.35, timeout=4)
            assert all(v > 0 for t, v in self.node.commands if t >= at), "happy path stopped"
            self.metrics["goal_x"] = self.node.base.x
        elif number == 2:
            self.move(2.0)
            self.until(lambda: any("decision" in outcome and outcome["decision"]["verdict"] == "jeol"
                                   for _, outcome in self.node.outcomes))
            self.until(lambda: any(status["stop"] == "revoked"
                                   for _, status in self.node.states), timeout=4)
            self.until(lambda: self.node.base.speed == 0.0, timeout=3)
            assert not any(row["zone_entry"] for row in self.node.trace), "base entered exclusion zone"
            self.metrics["max_command"] = max(v for _, v in self.node.commands)
            assert self.metrics["max_command"] <= 1.0
        elif number == 3:
            self.move(0.8)
            self.until(lambda: self.node.base.speed >= 0.7)
            self.node.human = (self.node.base.x + 1.3, self.node.base.y)
            first = self.until(lambda: any(has_person for _, has_person in self.node.world_events))
            self.events["first_person_world_s"] = next(t for t, person in self.node.world_events if person)
            zero = self.until(lambda: self.command_since(first, lambda v: v == 0.0) is not None,
                              timeout=1.0)
            self.metrics["gate_zero_ms"] = round((self.command_since(first, lambda v: v == 0.0) - first) * 1000, 1)
            self.until(lambda: self.node.base.speed == 0.0, timeout=2)
            self.metrics["minimum_gap_m"] = round(min(row["gap"] for row in self.node.trace
                                                       if row["gap"] is not None), 3)
            self.metrics["physical_stop_ms"] = stop_latency_ms(self.node.trace, first)
            assert self.metrics["minimum_gap_m"] >= 0.3, "child gap too small"
            assert self.metrics["gate_zero_ms"] <= 100, "gate revoke exceeded CI bound"
            assert zero >= first
        elif number == 4:
            self.move(0.8)
            at = time.monotonic()
            self.node.fault("hold")
            self.until(lambda: self.node.states and self.node.states[-1][1]["mode"] == "hold")
            self.until(lambda: self.command_since(at, lambda v: v == 0.0) is not None)
            self.metrics["gate_zero_ms"] = round((self.command_since(at, lambda v: v == 0.0) - at) * 1000, 1)
            before = len(self.node.commands)
            self.until(lambda: len(self.node.commands) >= before + 5)
            assert all(v == 0.0 for _, v in self.node.commands[before:])
        elif number == 5:
            self.move(0.8)
            self.node.drop_world = True
            at = time.monotonic()
            self.until(lambda: self.command_since(at, lambda v: v == 0.0) is not None, timeout=1)
            self.metrics["gate_zero_ms"] = round((self.command_since(at, lambda v: v == 0.0) - at) * 1000, 1)
            self.node.drop_world = False
            restored = time.monotonic()
            self.until(lambda: any(t >= restored for t, _ in self.node.world_events))
            self.until(lambda: time.monotonic() - restored > 0.35)
            assert all(v == 0.0 for t, v in self.node.commands if t >= restored)
            assert self.metrics["gate_zero_ms"] <= 300
        elif number == 6:
            self.move(0.8)
            self.stream = None
            at = self.last_proposal
            self.until(lambda: self.command_since(at, lambda v: v == 0.0) is not None, timeout=1)
            self.metrics["gate_zero_ms"] = round((self.command_since(at, lambda v: v == 0.0) - at) * 1000, 1)
            assert self.metrics["gate_zero_ms"] <= 300
        elif number == 7:
            self.move(0.8)
            self.until(lambda: self.node.base.speed >= 0.7)
            self.stream = None
            at = time.monotonic()
            self.stop(force=True)
            self.until(lambda: any(row["time_s"] >= at and row["deadman"]
                                   for row in self.node.trace), timeout=1, expected_exit=True)
            self.metrics["node_kill_deadman_ms"] = round((next(row["time_s"] for row in self.node.trace
                  if row["time_s"] >= at and row["deadman"]) - at) * 1000, 1)
            self.until(lambda: self.node.base.speed == 0.0, timeout=2, expected_exit=True)
            assert self.metrics["node_kill_deadman_ms"] <= 300
            # Fresh gate after an unclean exit remains in Hold; reset offline for
            # an independent Rust-child kill experiment in the same scenario.
            reset = [self.binary, "state", "set", "--state", str(self.root / "state.json"),
                     "--mode", "normal", "--by", "ci", "--reason", "second kill case"]
            self.until(lambda: subprocess.run(reset, capture_output=True).returncode == 0,
                       timeout=2, expected_exit=True)
            self.start()
            self.node.states.clear()
            self.ready()
            self.move(0.5)
            child_file = Path(f"/proc/{self.gate.pid}/task/{self.gate.pid}/children")
            self.until(lambda: child_file.exists() and child_file.read_text().strip())
            child_pid = int(child_file.read_text().split()[0])
            at = time.monotonic()
            os.kill(child_pid, signal.SIGKILL)
            self.until(lambda: self.gate.poll() == 2, timeout=3, expected_exit=True)
            zero = self.command_since(at, lambda v: v == 0.0)
            assert zero is not None, "bridge exited without delivering a zero command"
            self.metrics["child_kill_zero_ms"] = round((zero - at) * 1000, 1)
        elif number == 8:
            self.move(0.5)
            self.node.fault("estop")
            self.until(lambda: self.node.states and self.node.states[-1][1]["mode"] == "estop")
            self.stop(force=True)
            self.start()
            restarted = time.monotonic()
            self.stream = 0.8
            self.until(lambda: any(t >= restarted and status["mode"] == "estop"
                                   for t, status in self.node.states))
            self.until(lambda: time.monotonic() - restarted > 0.4)
            assert all(v == 0.0 for t, v in self.node.commands if t >= restarted)
            self.metrics["restarted_mode"] = "estop"
        else:
            raise ValueError("scenario must be 1..8")

    def verify_log(self):
        log = self.root / "sillok.jsonl"
        if not log.exists():
            if self.scenario == 1:
                return {"present": False}
            raise AssertionError("missing sillok log")
        report = subprocess.run([self.binary, "sillok", "verify", "--log", str(log),
                                 "--pubkey", public(9)], capture_output=True, text=True)
        if report.returncode:
            raise AssertionError("sillok verification failed: " + report.stdout + report.stderr)
        result = json.loads(report.stdout)
        if not result["fully_sealed"]:
            raise AssertionError("sillok is not fully sealed: " + report.stdout)
        return result


def write_artifacts(out, harness, result):
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    with (out / "trajectory.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time_s", "x", "y", "speed", "target",
                                                "deadman", "gap", "zone_entry"])
        writer.writeheader()
        writer.writerows(harness.node.trace)
    points = " ".join(f"{30 + 70*(row['x']-5):.1f},{140 - 100*(row['y']-5):.1f}"
                      for row in harness.node.trace)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="260" '
           'viewBox="0 0 640 260"><rect width="640" height="260" fill="white"/>'
           '<rect x="170" y="40" width="70" height="200" fill="#fdd"/>'
           f'<polyline fill="none" stroke="#146" stroke-width="2" points="{points}"/>'
           '<text x="12" y="20" font-size="14">Haetae reference base path</text></svg>')
    (out / "trajectory.svg").write_text(svg)
    stderr = harness.root / "gate.stderr"
    if stderr.exists():
        shutil.copy2(stderr, out / "gate.stderr")
    log = harness.root / "sillok.jsonl"
    if log.exists():
        shutil.copy2(log, out / "sillok.jsonl")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=lambda value: str(Path(value).resolve()))
    parser.add_argument("scenario", type=int, choices=range(1, 9))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fixture(root, args.binary)
        rclpy.init()
        harness = Harness(root, args.binary, args.scenario)
        error = None
        try:
            harness.run()
            log = harness.verify_log()
        except Exception as exc:
            error = str(exc)
            log = None
        finally:
            harness.stop(force=True)
            result = {"scenario": args.scenario, "repeat": args.repeat,
                      "ok": error is None, "error": error,
                      "metrics": harness.metrics, "events": harness.events,
                      "trace_samples": len(harness.node.trace), "sillok": log}
            write_artifacts(args.out, harness, result)
            harness.node.destroy_node()
            rclpy.shutdown()
    print(json.dumps(result))
    if error:
        sys.exit(1)


if __name__ == "__main__":
    main()
