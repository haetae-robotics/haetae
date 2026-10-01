"""Container-only boundary: non-root roles can send local DDS UDP only.

The host, root simulator/perception and provisioner remain trusted. This is
not a general-purpose OS sandbox. A missing kernel capability fails startup.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def sandboxed(argv):
    return [sys.executable, str(Path(__file__).with_name("role_sandbox.py")), *argv]


def dds_ports(domain):
    domain = int(domain)
    if not 0 <= domain <= 231:
        raise ValueError("network isolation supports ROS domains 0..231")
    return 7400 + 250 * domain, 7400 + 250 * domain + 249


class NetworkGuard:
    def __init__(self, domain):
        if os.geteuid() != 0 or not Path("/.dockerenv").exists():
            raise RuntimeError("network isolation requires the root-run Linux container")
        self.chain = "HAETAE_" + uuid.uuid4().hex[:12]
        self.installed = []
        self.ports = dds_ports(domain)
        addresses = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr"], text=True))
        self.destinations = sorted({"127.0.0.1", "239.255.0.1", *(
            info["local"] for interface in addresses for info in interface["addr_info"]
            if info.get("family") == "inet")})
        try:
            for binary in ("iptables", "ip6tables"):
                self.rule(binary, "-N", self.chain)
                self.installed.append(binary)
                if binary == "iptables":
                    for destination in self.destinations:
                        self.rule(binary, "-A", self.chain, "-p", "udp", "-d", destination,
                                  "--dport", f"{self.ports[0]}:{self.ports[1]}", "-j", "ACCEPT")
                self.rule(binary, "-A", self.chain, "-j", "REJECT")
                self.rule(binary, "-I", "OUTPUT", "1", "-m", "owner", "!", "--uid-owner", "0",
                          "-j", self.chain)
        except (OSError, subprocess.CalledProcessError) as exc:
            self.close()
            raise RuntimeError("cannot install network guard; run Docker with --cap-add=NET_ADMIN") from exc

    @staticmethod
    def rule(binary, *argv):
        return subprocess.run([binary, "-w", "2", *argv], check=True,
                              capture_output=True, text=True, timeout=5)

    def evidence(self):
        return {"ok": True, "scope": "container_nonroot_local_dds_only",
                "dds_ports": list(self.ports), "destinations": self.destinations,
                "rules": {binary: self.rule(binary, "-S", self.chain).stdout.splitlines()
                          for binary in self.installed}}

    def close(self):
        # Only call after all restricted descendants have stopped.
        for binary in reversed(self.installed):
            for argv in (("-D", "OUTPUT", "-m", "owner", "!", "--uid-owner", "0", "-j", self.chain),
                         ("-F", self.chain), ("-X", self.chain)):
                subprocess.run([binary, "-w", "2", *argv], capture_output=True, timeout=5)
        self.installed.clear()
