#!/usr/bin/env python3
"""Board-free process tests. The observer reads the native guard, never GPIO."""
import hashlib
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import sys
import time

from bench_link import LinkError, SerialLink
from bench_gate import BenchGate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "bench"


def wait_for(check, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.005)
    raise AssertionError("timed out waiting for positive control or stop evidence")


class Device:
    def __init__(self, name):
        self.audit = OUT / (name + ".jsonl")
        self.audit.unlink(missing_ok=True)
        master, slave = pty.openpty()
        self.port = os.ttyname(slave)
        self.child = subprocess.Popen([str(OUT / "device"), str(self.audit)], stdin=master, stdout=master)
        os.close(master)
        os.close(slave)
        wait_for(lambda: self.audit.exists())

    def events(self):
        lines = self.audit.read_text().splitlines(keepends=True)
        return [json.loads(line) for line in lines if line.endswith("\n")]

    def on(self):
        return next((e for e in reversed(self.events()) if e["on"]), None)

    def stopped(self, after, reason=None):
        return next((e for e in self.events() if not e["on"] and e["device_ms"] >= after
                     and (reason is None or e["reason"] == reason)), None)

    def close(self):
        self.child.kill()
        self.child.wait(timeout=2)


def protocol_case(name, attack, reason):
    device = Device(name)
    link = None
    try:
        link = SerialLink(device.port)
        link.exchange("HELLO")
        link.exchange("ARM")
        link.exchange("RUN")
        positive = wait_for(device.on)
        attack(link)
        stopped = wait_for(lambda: device.stopped(positive["device_ms"], reason))
        # No renewed output after rejection in this same session.
        try:
            link.exchange("RUN")
        except LinkError:
            pass
        time.sleep(.025)
        events = device.events()
        assert not any(e["on"] for e in events[events.index(stopped) + 1:])
        return {"case": name, "passed": True, "positive_control": True, "stop_reason": stopped["reason"],
                "last_run_to_off_ms": stopped["device_ms"] - device.on()["device_ms"]}
    finally:
        if link:
            link.close()
        device.close()


def host_case(name, scenario="allow", interrupt=None):
    device = Device(name)
    host = None
    gate_pid = None
    ready = OUT / (name + "-ready.json")
    ready.unlink(missing_ok=True)
    try:
        with (OUT / (name + "-host.log")).open("w") as log:
            host = subprocess.Popen([sys.executable, str(ROOT / "haetae-bench"), "run", "--port", device.port,
                                     "--binary", str(ROOT / "target" / "release" / "haetae"),
                                     "--scenario", scenario, "--seconds", "1", "--ready-file", str(ready)],
                                    stdout=log, stderr=log, start_new_session=True)
            def is_ready():
                if host.poll() is not None:
                    raise AssertionError("host failed before positive control; see " + str(log.name))
                if ready.exists():
                    try:
                        return json.loads(ready.read_text())
                    except json.JSONDecodeError:
                        pass
                return None
            gate_pid = wait_for(is_ready)["gate_pid"]
            positive = wait_for(device.on)
            if interrupt == "kill":
                host.kill()
            elif interrupt == "pause":
                os.kill(host.pid, signal.SIGSTOP)
            elif interrupt == "gate-kill":
                os.kill(gate_pid, signal.SIGKILL)
            expected = "lease" if interrupt in ("kill", "pause") else "stop"
            stopped = wait_for(lambda: device.stopped(positive["device_ms"], expected))
            if interrupt == "pause":
                os.kill(host.pid, signal.SIGCONT)
            code = host.wait(timeout=3)
            if interrupt in (None, "pause"):
                assert code == 0, "host scenario failed; see " + str(log.name)
            else:
                assert code != 0, "interrupted host unexpectedly reported success"
            time.sleep(.05)
            events = device.events()
            assert not any(e["on"] for e in events[events.index(stopped) + 1:])
            last_on = next(e for e in reversed(events) if e["on"])
            delay = stopped["device_ms"] - last_on["device_ms"]
            # Core deadline is exactly 200ms; this process observation allows 100ms
            # scheduling/PTY overhead. It is not a physical timing acceptance gate.
            assert 0 <= delay <= 300, f"native process stop too late: {delay}ms"
            return {"case": name, "passed": True, "positive_control": True,
                    "stop_reason": stopped["reason"], "last_run_to_off_ms": delay, "host_exit": code}
    finally:
        if host and host.poll() is None:
            os.kill(host.pid, signal.SIGCONT)
            host.kill()
            host.wait(timeout=2)
        if host:
            try:
                os.killpg(host.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        device.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "kind": "software-bench", "physical_hardware_tested": False,
              "electrical_output_measured": False, "usb_and_watchdog_tested": False,
              "lease_ms": 200, "native_observation_limit_ms": 300, "passed": False, "cases": []}
    report["source_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    source_paths = sorted((ROOT / "hardware").rglob("*.h")) + sorted((ROOT / "hardware").rglob("*.ino"))
    report["firmware_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    try:
        subprocess.run(["cargo", "build", "--release", "--locked", "-p", "haetae"], cwd=ROOT, check=True)
        report["gate_sha256"] = hashlib.sha256((ROOT / "target" / "release" / "haetae").read_bytes()).hexdigest()
        # Process loading may take >50ms while the device is still OFF. This
        # allowance must end before any positive gate/USB cycle is possible.
        wrapper = OUT / "slow-start-gate"
        binary = str(ROOT / "target" / "release" / "haetae")
        wrapper.write_text("#!/usr/bin/env python3\nimport os,sys,time\ntime.sleep(.12)\n"
                           + f"os.execv({binary!r}, [{binary!r}] + sys.argv[1:])\n")
        wrapper.chmod(0o755)
        gate = BenchGate(wrapper)
        try:
            assert gate.bridge.timeout == .050
            assert gate.cycle()
            gate.remaining()
            report["off_only_slow_bootstrap_passed"] = True
        finally:
            gate.close()
        for source, target in (("guard_test.cpp", "guard-test"), ("device.cpp", "device")):
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O2",
                            str(ROOT / "hardware" / "native" / source), "-o", str(OUT / target)], check=True)
        subprocess.run([str(OUT / "guard-test")], check=True)
        report["core_tests_passed"] = True
        cases = [
            ("host-kill", lambda: host_case("host-kill", interrupt="kill")),
            ("host-pause-resume", lambda: host_case("host-pause-resume", interrupt="pause")),
            ("rust-kill", lambda: host_case("rust-kill", interrupt="gate-kill")),
        ]
        for scenario in ("person", "world-loss", "replay", "invalid-signature", "allow"):
            cases.append((scenario, lambda s=scenario: host_case(s, s)))
        def raw(link, content):
            os.write(link.fd, content)
        cases.extend([
            ("usb-replay", lambda: protocol_case("usb-replay", lambda l: raw(l,
                f"H1 RUN {l.session} {l.seq} {l.challenge - 1}\n".encode()), "replay")),
            ("stale-queued-run", lambda: protocol_case("stale-queued-run", lambda l: (
                time.sleep(.11), raw(l, f"H1 RUN {l.session} {l.seq + 1} {l.challenge}\n".encode())), "stale")),
            ("oversized-frame", lambda: protocol_case("oversized-frame", lambda l: raw(l, b"x" * 1000 + b"\n"), "protocol")),
            ("partial-frame", lambda: protocol_case("partial-frame", lambda l: raw(l, b"H1 RUN "), "partial")),
        ])
        for name, test in cases:
            result = test()
            report["cases"].append(result)
            print(f"PASS {name}: {result['stop_reason']} / {result['last_run_to_off_ms']} ms", flush=True)
        report["passed"] = True
    except BaseException as exc:
        report["error"] = str(exc)
        raise
    finally:
        (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Software-only report: " + str(OUT / "report.json"))


if __name__ == "__main__":
    main()
