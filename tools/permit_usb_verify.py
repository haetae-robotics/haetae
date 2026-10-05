"""USB-only qualification; MCU ACKs, never electrical or motor measurements."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import fcntl
import struct

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from permit_keys import deployment, private_bytes
from permit_link import PermitLink
from permit_protocol import payload, frame
from permit_verify import bind_controller
from permit_checks import require
from permit_process import stop_pair

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/controller-permit-usb"


def interfaces():
    """Read only USB interface classes; never serialize device IDs/other devices."""
    if sys.platform == "darwin":
        roots = plistlib.loads(subprocess.check_output(["/usr/sbin/ioreg", "-a", "-l"]))
        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in value.get("IORegistryEntryChildren", []):
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)
        boards = [v for v in walk(roots) if v.get("idVendor") == 0x2341 and v.get("idProduct") == 0x0069
                  and "bDeviceClass" in v]
        if len(boards) != 1:
            raise ValueError("USB descriptor check needs exactly one runtime UNO R4 Minima")
        return sorted({v["bInterfaceClass"] for v in walk(boards[0]) if "bInterfaceNumber" in v})
    if sys.platform == "linux":
        boards = []
        for p in Path("/sys/bus/usb/devices").iterdir():
            if (p / "idVendor").exists() and (p / "idProduct").exists():
                if (p / "idVendor").read_text().strip() == "2341" and (p / "idProduct").read_text().strip() == "0069":
                    boards.append(p)
        if len(boards) != 1:
            raise ValueError("USB descriptor check needs exactly one runtime UNO R4 Minima")
        return sorted({int((p / "bInterfaceClass").read_text().strip(), 16)
                       for p in boards[0].glob(boards[0].name + ":*") if (p / "bInterfaceClass").exists()})
    raise ValueError("USB qualification supports macOS/Linux")


def raw_reply(link, raw, timeout=.05):
    started = time.monotonic()
    if os.write(link.fd, raw) != len(raw):
        raise OSError("short test write")
    response = b""
    while b"\n" not in response:
        left = timeout - (time.monotonic() - started)
        if left <= 0 or not select.select([link.fd], [], [], left)[0]:
            raise RuntimeError("test USB ACK timed out")
        response += os.read(link.fd, 160)
        if len(response) > 160:
            raise ValueError("oversized test reply")
    return response.decode("ascii").strip().split(" "), (time.monotonic() - started)*1000


def protocol_case(port, config, key, name):
    link = PermitLink(port, config["install"])
    timings = []
    try:
        time.sleep(.5)
        link.query("HELLO")
        session_key = bind_controller(link, key, config)
        def sign(request):
            return hashlib.blake2b(payload(request), key=session_key, digest_size=32).hexdigest()
        for op in ("ARM", "RUN"):
            request = dict(link.current, op=op)
            signature = sign(request)
            started = time.monotonic()
            link.execute(request, {"kind": "permit", "signature": signature, "duration": 200, "remaining_ms": 50}, .05)
            timings.append((time.monotonic()-started)*1000)
        if name == "STATUS_does_not_renew":
            started = time.monotonic()
            while True:
                time.sleep(.02)
                if link.query("STATUS") == "LOCKED":
                    break
                if time.monotonic()-started > .3:
                    raise AssertionError("MCU lease did not lock without RUN/STOP")
            return {"case": name, "passed": True, "positive_USB_ON": True,
                    "no_RUN_or_STOP_before_lock": True,
                    "RUN_ACK_to_LOCKED_USB_observation_wall_ms": round((time.monotonic()-started)*1000, 3),
                    "positive_round_trip_ms": timings}
        if name == "replayed_permit":
            raw = frame(request, signature)
            reason = "replay"
        else:
            request = dict(link.current, op="RUN")
            altered = dict(request)
            if name == "wrong_epoch":
                altered["epoch"] += 1
            elif name == "wrong_action":
                altered["op"] = "ARM"
            elif name == "future_nonce":
                altered["nonce"] = "00"*16
            mac = "00"*32 if name == "forged_MAC" else sign(altered)
            raw = frame(request, mac)
            reason = "signature"
        response, duration = raw_reply(link, raw)
        require(len(response) == 11 and response[:3] == ['H2', 'ERR', config['install']], 'permit_usb_verify.py: qualification predicate failed')
        require(response[-2:] == ['LOCKED', reason], 'permit_usb_verify.py: qualification predicate failed')
        require(link.query('STATUS') == 'LOCKED', 'permit_usb_verify.py: qualification predicate failed')
        fresh = dict(link.current, op="RUN")
        response, _ = raw_reply(link, frame(fresh, sign(fresh)))
        require(response[1] == 'ERR' and response[-2] == 'LOCKED', 'permit_usb_verify.py: qualification predicate failed')
        return {"case": name, "passed": True, "positive_USB_ON": True,
                "negative_USB_LOCKED": True, "same_session_fresh_permit_rejected": True,
                "positive_round_trip_ms": timings, "rejection_round_trip_ms": duration}
    finally:
        link.stop()
        link.close()


def host_case(port, directory, config, scenario, pause=False):
    name = "relay_pause_resume" if pause else scenario
    with tempfile.TemporaryDirectory(prefix="h2-") as tmp:
        tmp = Path(tmp)
        ready, relay_ready = tmp / "ready", tmp / "relay-ready"
        auth = relay = None
        try:
            with (OUT / (name + "-authorizer.log")).open("w") as alog, (OUT / (name + "-relay.log")).open("w") as rlog:
                auth = subprocess.Popen([sys.executable, str(ROOT / "haetae-permit"), "authorizer", "--deployment",
                    str(directory / "deployment.json"), "--key", str(directory / "authorizer.seed"),
                    "--socket", str(tmp / "auth.sock"), "--scenario", scenario, "--seconds", "1",
                    "--ready-file", str(ready)], stdout=alog, stderr=alog, start_new_session=True)
                deadline = time.monotonic()+5
                while not ready.exists():
                    if auth.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("physical authorizer failed before ready")
                    time.sleep(.005)
                relay = subprocess.Popen([sys.executable, str(ROOT / "haetae-permit"), "relay", "--port", port,
                    "--deployment", str(directory / "deployment.json"), "--authorizer-socket", str(tmp / "auth.sock"),
                    "--ready-file", str(relay_ready)], stdout=rlog, stderr=rlog)
                deadline = time.monotonic()+3
                while not relay_ready.exists():
                    if relay.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("physical relay failed before positive ON")
                    time.sleep(.005)
                if pause:
                    os.kill(relay.pid, signal.SIGSTOP)
                    time.sleep(.6)
                    os.kill(relay.pid, signal.SIGCONT)
                code = relay.wait(timeout=4)
                require(code != 0 if pause else code == 0, 'permit_usb_verify.py: qualification predicate failed')
                if not pause:
                    text = (OUT / (name + "-authorizer.log")).read_text()
                    require('Authorizer fault injected: ' + scenario in text, 'permit_usb_verify.py: qualification predicate failed')
                    require('Authorizer terminal:' in text, 'permit_usb_verify.py: qualification predicate failed')
                require((OUT / (name + '-relay.log')).read_text().count('허용:') == 1, 'permit_usb_verify.py: qualification predicate failed')
                link = PermitLink(port, config["install"])
                try:
                    require(link.query('STATUS') == 'LOCKED', 'permit_usb_verify.py: qualification predicate failed')
                finally:
                    link.close()
                return {"case": name, "passed": True, "positive_USB_ON": True,
                        "post_stop_USB_LOCKED": True, "automatic_rearm": False, "relay_exit": code}
        finally:
            stop_pair(auth, relay)


def no_1200_reset(port, config):
    link = PermitLink(port, config["install"])
    try:
        link.query("HELLO")
        old = dict(link.current)
        link.stop()
        attrs = termios.tcgetattr(link.fd)
        attrs[4] = attrs[5] = termios.B1200
        termios.tcsetattr(link.fd, termios.TCSANOW, attrs)
        fcntl.ioctl(link.fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_DTR))
        time.sleep(.2)
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(link.fd, termios.TCSANOW, attrs)
        fcntl.ioctl(link.fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
        require(link.query('STATUS') == 'LOCKED', 'permit_usb_verify.py: qualification predicate failed')
        require(link.current['epoch'] == old['epoch'] and link.current['generation'] == old['generation'], 'permit_usb_verify.py: qualification predicate failed')
        return {"case": "runtime_1200_baud_no_reset", "passed": True, "boot_and_generation_unchanged": True}
    finally:
        link.stop()
        link.close()


def main(port, directory):
    directory = Path(directory).resolve()
    config = deployment(directory / "deployment.json")
    key = Ed25519PrivateKey.from_private_bytes(private_bytes(directory / "authorizer.seed"))
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"scope": "physical_UNO_R4_H2_USB_LED_only", "passed": False, "cases": [],
              "physical_hardware_tested": True, "inputs": "trusted synthetic signed fixture",
              "host_profile": "same-UID trusted developer host; key isolation tested separately on Linux",
              "electrical_output_measured": False, "watchdog_reset_tested": False,
              "flash_power_cut_tested": False, "robot_motor_stop_tested": False, "secure_boot_claimed": False,
              "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())}
    try:
        report["gate_sha256"] = hashlib.sha256((ROOT / "target/release/haetae").read_bytes()).hexdigest()
        manifest = json.loads((ROOT / "artifacts/controller-permit-build/manifest.json").read_text())
        sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((ROOT / "hardware/uno_r4_permit").rglob("*")) if p.is_file()}
        require(all(manifest[k] == config[k] for k in ('install', 'public_key', 'controller_public_key')), 'firmware deployment does not match device directory')
        require(manifest['resolved_core'] == 'haetae_permit' and manifest['firmware_source_sha256'] == sources, 'permit_usb_verify.py: qualification predicate failed')
        report["firmware_source_sha256"] = sources
        report["build_manifest"] = {k: v for k, v in manifest.items() if k not in ("install", "public_key")}
        classes = interfaces()
        require(classes == [2, 10], 'runtime USB must have CDC only; DFU still exposed')
        report["USB_interface_classes"] = classes
        report["cases"].append({"case": "runtime_DFU_descriptor_absent", "passed": True})
        for name in ("forged_MAC", "replayed_permit", "wrong_epoch", "wrong_action", "future_nonce", "STATUS_does_not_renew"):
            report["cases"].append(protocol_case(port, config, key, name))
        for scenario in ("person", "world-loss", "replay", "invalid-signature"):
            report["cases"].append(host_case(port, directory, config, scenario))
        report["cases"].append(host_case(port, directory, config, "allow", pause=True))
        report["cases"].append(no_1200_reset(port, config))
        report["passed"] = True
    finally:
        (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("H2 USB LED qualification passed: " + str(len(report["cases"])) + " cases. No electrical/motor claim.")
