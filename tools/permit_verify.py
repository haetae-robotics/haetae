"""Actual native controller crypto + Rust authorizer + untrusted relay tests."""
import hashlib
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import sys
import tempfile
import termios
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import serialization

from bench_verify import wait_for
from permit_keys import private_bytes, provision
from permit_link import PermitLink
from permit_protocol import frame, payload, bind_payload

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/controller-permit"


def compile_native():
    OUT.mkdir(parents=True, exist_ok=True)
    vendor = ROOT / "hardware/uno_r4_permit/src/vendor"
    objects = []
    for name in ("monocypher", "monocypher-ed25519"):
        obj = OUT / (name + ".o")
        subprocess.run([os.environ.get("CC", "cc"), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(vendor), "-c", str(vendor / (name + ".c")), "-o", str(obj)], check=True)
        objects.append(str(obj))
    for name in ("permit_guard_test", "permit_device"):
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "hardware/native" / (name + ".cpp")), *objects, "-o", str(OUT / name)], check=True)
    subprocess.run([str(OUT / "permit_guard_test")], check=True)


class Device:
    def __init__(self, config, key_path, name, epoch=1):
        self.audit = OUT / (name + ".jsonl")
        self.audit.unlink(missing_ok=True)
        master, slave = pty.openpty()
        self.port = os.ttyname(slave)
        self.child = subprocess.Popen([str(OUT / "permit_device"), str(self.audit), config["install"],
                                       config["public_key"], str(key_path), str(epoch)], stdin=master, stdout=master)
        os.close(master)
        self.slave = slave  # Keep PTY alive until the client opens it.
        wait_for(lambda: self.audit.exists())

    def events(self):
        return [json.loads(row) for row in self.audit.read_text().splitlines(keepends=True) if row.endswith("\n")]

    def on(self):
        return next((e for e in reversed(self.events()) if e["on"]), None)

    def stopped(self, after, reason=None):
        return next((e for e in self.events() if not e["on"] and e["device_ms"] >= after
                     and (reason is None or e["reason"] == reason)), None)

    def close(self):
        self.child.kill()
        self.child.wait(timeout=2)
        os.close(self.slave)


def bind_controller(link, key, config):
    request = dict(link.current, op="BIND")
    ephemeral_key = X25519PrivateKey.generate()
    ephemeral = ephemeral_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    bytes_ = bind_payload(request, ephemeral, config["controller_public_key"])
    shared = ephemeral_key.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(config["controller_public_key"])))
    session_key = hashlib.blake2b(bytes_, key=shared, digest_size=32).digest()
    link.execute(request, {"allowed": True, "signature": key.sign(bytes_).hex(), "ephemeral": ephemeral,
                           "duration": 0, "remaining_ms": 500}, .5)
    return session_key


def protocol_case(directory, config, name, attack, reason):
    device = Device(config, directory / "controller.seed", name)
    link = PermitLink(device.port, config["install"])
    key = Ed25519PrivateKey.from_private_bytes(private_bytes(directory / "authorizer.seed"))
    session_key = None
    def sign(value):
        return hashlib.blake2b(payload(value), key=session_key, digest_size=32).hexdigest()
    def approve(op="RUN"):
        request = dict(link.current, op=op)
        signature = sign(request)
        link.execute(request, {"allowed": True, "signature": signature, "duration": 200, "remaining_ms": 50}, .05)
        return request, signature
    try:
        link.query("HELLO")
        session_key = bind_controller(link, key, config)
        approve("ARM")
        request, signature = approve()
        positive = wait_for(device.on)
        attack(link, sign, request, signature)
        stopped = wait_for(lambda: device.stopped(positive["device_ms"], reason))
        if reason == "partial":
            os.write(link.fd, b"\n")  # Finish discard mode; it never re-arms.
            time.sleep(.005)
        termios.tcflush(link.fd, termios.TCIFLUSH)  # Discard the expected raw attack ERR.
        # The same session cannot recover using a freshly signed RUN either.
        link.query("STATUS")
        try:
            approve()
        except RuntimeError:
            pass
        time.sleep(.025)
        assert not any(e["on"] for e in device.events()[device.events().index(stopped) + 1:])
        return {"case": name, "passed": True, "positive_control": True, "stop_reason": stopped["reason"]}
    finally:
        link.close()
        device.close()


def host_case(directory, config, name, scenario="allow", pause=False, kill_authorizer=False):
    device = Device(config, directory / "controller.seed", name)
    with tempfile.TemporaryDirectory(prefix="haetae-h2-ipc-") as tmp:
        tmp = Path(tmp)
        socket_path, ready, relay_ready = tmp / "auth.sock", tmp / "auth.ready", tmp / "relay.ready"
        authorizer = relay = None
        try:
            with (OUT / (name + "-authorizer.log")).open("w") as auth_log, (OUT / (name + "-relay.log")).open("w") as relay_log:
                authorizer = subprocess.Popen([sys.executable, str(ROOT / "haetae-permit"), "authorizer",
                    "--deployment", str(directory / "deployment.json"), "--key", str(directory / "authorizer.seed"),
                    "--socket", str(socket_path), "--scenario", scenario, "--seconds", "1", "--ready-file", str(ready)],
                    stdout=auth_log, stderr=auth_log, start_new_session=True)
                def auth_ready():
                    if authorizer.poll() is not None:
                        raise AssertionError("authorizer failed before ready: " + str(auth_log.name))
                    return ready.exists()
                wait_for(auth_ready, 5)
                relay = subprocess.Popen([sys.executable, str(ROOT / "haetae-permit"), "relay", "--port", device.port,
                    "--deployment", str(directory / "deployment.json"), "--authorizer-socket", str(socket_path),
                    "--ready-file", str(relay_ready)], stdout=relay_log, stderr=relay_log)
                def positive_ready():
                    if relay.poll() is not None:
                        raise AssertionError("relay failed before ON: " + str(relay_log.name))
                    return relay_ready.exists() and device.on()
                positive = wait_for(positive_ready)
                if pause:
                    os.kill(relay.pid, signal.SIGSTOP)
                    time.sleep(.6)
                    os.kill(relay.pid, signal.SIGCONT)
                if kill_authorizer:
                    authorizer.kill()
                code = relay.wait(timeout=4)
                stopped = wait_for(lambda: device.stopped(positive["device_ms"]))
                assert stopped and (not pause and not kill_authorizer or code != 0)
                if not pause and not kill_authorizer:
                    assert code == 0, "scenario failed: " + str(relay_log.name)
                    auth_text = Path(auth_log.name).read_text()
                    if scenario == "allow":
                        assert "Authorizer terminal: operator_complete" in auth_text
                    else:
                        assert "Authorizer fault injected: " + scenario in auth_text, "stopped before intended fault"
                        assert "Authorizer terminal: " in auth_text
                time.sleep(.05)
                events = device.events()
                assert not any(e["on"] for e in events[events.index(stopped)+1:])
                last_on = next(e for e in reversed(events) if e["on"])
                delay = stopped["device_ms"] - last_on["renewed_ms"]
                assert 0 <= delay <= 300
                return {"case": name, "passed": True, "positive_control": True,
                        "stop_reason": stopped["reason"], "last_run_to_off_ms": delay, "relay_exit": code}
        finally:
            for child in (relay, authorizer):
                if child and child.poll() is None:
                    os.kill(child.pid, signal.SIGCONT)
                    child.kill()
                if child:
                    child.wait(timeout=2)
            if authorizer:
                try:
                    os.killpg(authorizer.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            device.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"schema": 2, "kind": "signed-controller-software-bench", "passed": False,
              "physical_hardware_tested": False, "electrical_output_measured": False,
              "flash_power_cut_tested": False, "private_key_isolation_tested": False, "cases": []}
    report["source_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    sources = sorted((ROOT / "hardware/uno_r4_permit").rglob("*"))
    report["firmware_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources if p.is_file()}
    try:
        subprocess.run(["cargo", "build", "--release", "--locked", "-p", "haetae"], cwd=ROOT, check=True)
        report["gate_sha256"] = hashlib.sha256((ROOT / "target/release/haetae").read_bytes()).hexdigest()
        compile_native()
        report["core_crypto_epoch_tests_passed"] = True
        with tempfile.TemporaryDirectory(prefix="haetae-permit-keys-") as tmp:
            directory = Path(tmp) / "provision"
            config = provision(directory)
            def raw(link, bytes_):
                os.write(link.fd, bytes_)
            attacks = [
                ("unsigned-run", lambda l,k,r,s: raw(l, b"H2 RUN\n"), "protocol"),
                ("forged-signature", lambda l,k,r,s: raw(l, frame(dict(l.current, op="RUN"), "00"*32)), "signature"),
                ("replayed-permit", lambda l,k,r,s: raw(l, frame(r,s)), "replay"),
                ("wrong-epoch", lambda l,k,r,s: raw(l, frame(dict(l.current, op="RUN"), k(dict(l.current, op="RUN", epoch=2)))), "signature"),
                ("wrong-install", lambda l,k,r,s: raw(l, frame(dict(l.current, op="RUN"), k(dict(l.current, op="RUN", install="00"*16)))), "signature"),
                ("wrong-action", lambda l,k,r,s: raw(l, frame(dict(l.current, op="RUN"), k(dict(l.current, op="ARM")))), "signature"),
                ("predicted-future-nonce", lambda l,k,r,s: raw(l, frame(dict(l.current, op="RUN"), k(dict(l.current, op="RUN", nonce="00"*16)))), "signature"),
                ("expired-permit", lambda l,k,r,s: (time.sleep(.060), raw(l, frame(dict(l.current, op="RUN"), k(dict(l.current, op="RUN"))))), "stale"),
                ("status-never-renews", lambda l,k,r,s: [time.sleep(.06) or l.query("STATUS") for _ in range(4)], "lease"),
                ("partial-frame", lambda l,k,r,s: raw(l, b"H2 RUN "), "partial"),
            ]
            for name, attack, reason in attacks:
                report["cases"].append(protocol_case(directory, config, name, attack, reason))
            for scenario in ("allow", "person", "world-loss", "replay", "invalid-signature"):
                report["cases"].append(host_case(directory, config, scenario, scenario))
            report["cases"].append(host_case(directory, config, "relay-pause-resume", pause=True))
            report["cases"].append(host_case(directory, config, "authorizer-kill", kill_authorizer=True))
        report["passed"] = True
    finally:
        (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Signed controller software bench passed: " + str(len(report["cases"])) + " cases; no physical/motor claim.")


if __name__ == "__main__":
    main()
