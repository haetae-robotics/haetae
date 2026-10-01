"""Exercise real kernel restrictions and the live Gazebo pose service."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from network_guard import sandboxed


def native_attempt():
    from gz.transport13 import Node
    from native_person import _set_poses
    print(json.dumps({"native_attempt_started": True}), flush=True)
    node = Node()
    time.sleep(0.5)
    accepted = _set_poses(node, {"lidar_calibration": ((3, 7, -5), (0, 0, 0, 1))})
    print(json.dumps({"accepted": bool(accepted)}), flush=True)


def socket_attempts(allowed_port, denied_port):
    denied = {}
    for name, family, kind in (("tcp", socket.AF_INET, socket.SOCK_STREAM),
                               ("unix", socket.AF_UNIX, socket.SOCK_STREAM),
                               ("ipv6", socket.AF_INET6, socket.SOCK_DGRAM),
                               ("packet", socket.AF_PACKET, socket.SOCK_RAW)):
        try:
            connection = socket.socket(family, kind)
            connection.close()
            denied[name] = False
        except PermissionError:
            denied[name] = True
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.sendto(b"allowed", ("127.0.0.1", int(allowed_port)))
        try:
            udp.sendto(b"forbidden", ("127.0.0.1", int(denied_port)))
        except OSError:
            pass  # Receiver observation below is the delivery oracle.
    # An inherited exec must retain restrictions, not just this Python process.
    inherited = subprocess.run([sys.executable, "-c",
        "import socket; socket.socket(socket.AF_INET, socket.SOCK_STREAM)"],
        capture_output=True, timeout=3)
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                  if ":" in line)
    print(json.dumps({"uid": os.geteuid(), "denied": denied,
                      "exec_inherits": inherited.returncode != 0,
                      "no_new_privs": status["NoNewPrivs"].strip() == "1",
                      "capabilities_empty": all(int(status[key].strip(), 16) == 0
                          for key in ("CapEff", "CapPrm", "CapAmb"))}))


def _child(mode, args=(), uid=None):
    argv = [sys.executable, str(Path(__file__).resolve()), mode, *map(str, args)]
    options = {}
    if uid is not None:
        argv = sandboxed(argv)
        options = {"user": uid, "group": uid, "extra_groups": []}
    return subprocess.run(argv, capture_output=True, text=True, timeout=8, **options)


def probe_transport(world, guard, processes, wait_for):
    # A root positive control must alter the real scan, ruling out a dead
    # service, wrong partition or a probe that merely failed to import.
    positive = _child("native")
    if positive.returncode or not json.loads(positive.stdout.splitlines()[-1])["accepted"]:
        raise AssertionError("root Gazebo pose positive control failed: " + positive.stderr[-500:])
    wait_for(lambda: not world.sensor_info.get("healthy"), 3, processes,
             "root native pose request changes lidar coverage")
    if not world.native.calibration_visible(True):
        raise AssertionError("cannot restore calibration after transport positive control")
    wait_for(lambda: world.sensor_info.get("healthy"), 5, processes, "native scan restored")
    results = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as allowed, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as denied:
        allowed.bind(("127.0.0.1", guard.ports[1] - 1))
        denied.bind(("127.0.0.1", 0))
        allowed.settimeout(1)
        denied.settimeout(0.15)
        if guard.ports[0] <= denied.getsockname()[1] <= guard.ports[1]:
            raise AssertionError("negative receiver accidentally uses an allowed DDS port")
        # Root delivery to the same forbidden receiver proves the receiver works.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"root", denied.getsockname())
        if denied.recv(100) != b"root":
            raise AssertionError("root network positive control failed")
        for uid in (2001, 2002, 2003, 2004, 65534):
            basic = _child("sockets", (allowed.getsockname()[1], denied.getsockname()[1]), uid)
            if basic.returncode:
                raise AssertionError("sandbox probe failed: " + basic.stderr[-1000:])
            row = json.loads(basic.stdout.splitlines()[-1])
            if not (all(row["denied"].values()) and row["exec_inherits"] and
                    row["no_new_privs"] and row["capabilities_empty"] and allowed.recv(100) == b"allowed"):
                raise AssertionError("role socket restriction or authorized UDP failed")
            try:
                denied.recv(100)
                raise AssertionError("forbidden UDP reached root receiver")
            except socket.timeout:
                row["forbidden_udp_not_received"] = True
            attack = _child("native", uid=uid)
            started = '{"native_attempt_started": true}' in attack.stdout
            accepted = '"accepted": true' in attack.stdout
            if not started or accepted:
                raise AssertionError("native attack did not attempt the live service or succeeded")
            if not world.sensor_info.get("healthy"):
                raise AssertionError("native attacker modified scan coverage")
            row["native_pose_request_blocked"] = True
            row["native_exit_code"] = attack.returncode
            results[str(uid)] = row
    return {"ok": True, "root_native_request_observed_in_scan": True,
            "root_forbidden_port_positive_control": True, "principals": results,
            "scope": "sandboxed_container_roles_not_root_or_host", "firewall": guard.evidence()}


if __name__ == "__main__":
    if sys.argv[1] == "native":
        native_attempt()
    elif sys.argv[1] == "sockets":
        socket_attempts(*sys.argv[2:])
