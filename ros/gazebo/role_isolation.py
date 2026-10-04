"""Root-only test provisioning of isolated signer and gateway principals.

The provisioner, simulator and host remain trusted. Gateway actuation authority
is trusted; isolating world keys does not make a compromised actuator safe.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from network_guard import sandboxed
from safe_evidence import read_evidence, write_checkpoint

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

UIDS = {"gate": 2001, "world": 2002, "vla": 2003, "proposal": 2004}
ENCLAVES = {"gate": "gate", "world": "world", "vla": "vla_signer", "proposal": "vla"}


def fresh_fixture(root):
    """Replace public demo seeds with independent, unpredictable test keys."""
    policy = (root / "policy.json").read_bytes()
    keys = {}
    for role in ("world", "fault", "vla", "log", "root"):
        seed = os.urandom(32)
        key = Ed25519PrivateKey.from_private_bytes(seed)
        keys[role] = key
        if role != "root":
            (root / (role + ".key")).write_text(seed.hex())
            (root / (role + ".key")).chmod(0o600)
    public = lambda role: keys[role].public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    body = json.dumps({"v": 1, "audience": "gazebo-" + os.urandom(16).hex(), "epoch": 1,
                       "policy_sha256": hashlib.sha256(policy).hexdigest(),
                       "keys": {role: public(role) for role in ("world", "fault", "vla")}},
                      separators=(",", ":"))
    (root / "trust.json").write_text(json.dumps({"body": body, "signature": keys["root"].sign(
        b"haetae-trust-bundle-v1\0" + body.encode()).hex()}))
    return public("root"), public("log")


def private_tree(directory, uid):
    # Provision under a root-owned 0700 anchor, handing directories over last.
    entries = sorted((directory, *directory.rglob("*")), key=lambda p: len(p.parts), reverse=True)
    for entry in entries:
        is_directory = entry.is_dir()
        entry.chmod(0o700 if is_directory else 0o600)
        os.chown(entry, uid, uid, follow_symlinks=False)


class Roles:
    def __init__(self, root, keystore, binary, arm_joints):
        if os.geteuid() != 0 or not sys.platform.startswith("linux") or not Path("/.dockerenv").exists():
            raise RuntimeError("isolated roles require the root-run Linux test container")
        self.root = root
        self.keystore = keystore
        self.binary = binary
        self.arm_joints = list(arm_joints)
        self.root_public, self.log_public = fresh_fixture(root)
        root.chmod(0o711)
        self.directories = {}
        for role, uid in UIDS.items():
            directory = root / ("principal-" + role)
            directory.mkdir(mode=0o700)
            self.directories[role] = directory
            shutil.copytree(keystore / ("enclaves/haetae/" + ENCLAVES[role]),
                            directory / ("keystore/enclaves/haetae/" + ENCLAVES[role]))
            (directory / "logs").mkdir()
            (directory / "runtime").mkdir()
            shutil.copy2(root / "trust.json", directory / "trust.json")
            key_roles = ("world", "fault") if role == "world" else ("vla",) if role == "vla" else ("log",) if role == "gate" else ()
            for key in key_roles:
                shutil.copy2(root / (key + ".key"), directory / (key + ".key"))
            private_tree(directory, uid)
            # Pin principal entry names; only runtime/log and gate session children are writable.
            os.chown(directory, 0, 0)
            directory.chmod(0o711)
        self.configure_gate(root)
        for key in ("world", "fault", "vla", "log"):
            (root / (key + ".key")).unlink()

    def environment(self, role, base):
        directory = self.directories[role]
        return {**base, "ROS_SECURITY_KEYSTORE": str(directory / "keystore"),
                "ROS_SECURITY_ENCLAVE_OVERRIDE": "/haetae/" + ENCLAVES[role],
                "ROS_LOG_DIR": str(directory / "logs"), "HOME": str(directory)}

    def configure_gate(self, fixture_root):
        directory = self.directories["gate"] / fixture_root.name
        directory.mkdir(mode=0o700)  # Never reopen a role-controlled existing session.
        for name in ("policy.json", "state.json"):
            shutil.copy2(fixture_root / name, directory / name)
        shutil.copy2(self.root / "trust.json", directory / "trust.json")
        write_checkpoint(directory / "log.key", read_evidence(
            self.root, self.directories["gate"] / "log.key", UIDS["gate"]), mode=0o600)
        params = json.loads((fixture_root / "params.yaml").read_text())
        p = params["haetae_gate"]["ros__parameters"]
        p.update({"signed_inputs_only": True, "keys_json": "{}", "inputs_json": "[]",
                  "arm_inputs_json": "[]", "root_pubkey": self.root_public,
                  "policy_path": str(directory / "policy.json"),
                  "state_path": str(directory / "state.json"),
                  "trust_path": str(directory / "trust.json"),
                  "key_path": str(directory / "log.key"),
                  "sillok_path": str(directory / "sillok.jsonl"),
                  "use_sim_time": True, "heartbeat_topic": "/haetae_gate/heartbeat"})
        (directory / "params.yaml").write_text(json.dumps(params))
        private_tree(directory, UIDS["gate"])
        return directory

    def source_params(self, role):
        directory = self.directories[role]
        if (directory / "params.yaml").exists():
            return directory / "params.yaml"
        keys = ("world", "fault") if role == "world" else ("vla",)
        params = {"haetae_source_signer": {"ros__parameters": {
            "use_sim_time": True, "role": role, "trust_path": str(directory / "trust.json"),
            "counter_path": str(directory / "runtime/counters.json"),
            "keys_json": json.dumps({key: str(directory / (key + ".key")) for key in keys}),
            "arm_joints_json": json.dumps(self.arm_joints)}}}
        (directory / "params.yaml").write_text(json.dumps(params))
        os.chown(directory / "params.yaml", UIDS[role], UIDS[role])
        (directory / "params.yaml").chmod(0o600)
        return directory / "params.yaml"

    def counter(self, role):
        from safe_evidence import read_evidence_text
        path = self.directories[role] / "runtime/counters.json"
        return json.loads(read_evidence_text(self.root, path, UIDS[role]))["counters"][role]

    def probe_read_boundaries(self):
        forbidden = [str(self.directories["world"] / key) for key in (
            "world.key", "fault.key", "keystore/enclaves/haetae/world/key.pem")]
        result = {}
        for role in ("gate", "vla", "proposal"):
            code = "import json,sys; denied=[]\nfor p in sys.argv[1:]:\n try: open(p,'rb').read(); denied.append(False)\n except PermissionError: denied.append(True)\nprint(json.dumps(denied))"
            probe = subprocess.run([sys.executable, "-c", code, *forbidden], user=UIDS[role],
                                   group=UIDS[role], extra_groups=[], capture_output=True, text=True,
                                   check=True, timeout=5)
            denied = json.loads(probe.stdout)
            if not all(denied):
                raise AssertionError(role + " could read trusted perception credentials")
            other_keys = [str(entry) for owner, directory in self.directories.items() if owner != role
                          for entry in directory.rglob("*.key")]
            other_keys += [str(directory / ("keystore/enclaves/haetae/" + ENCLAVES[owner] + "/key.pem"))
                           for owner, directory in self.directories.items() if owner != role]
            cross = subprocess.run([sys.executable, "-c", code, *other_keys], user=UIDS[role],
                                   group=UIDS[role], extra_groups=[], capture_output=True, text=True,
                                   check=True, timeout=5)
            cross_denied = json.loads(cross.stdout)
            if not all(cross_denied):
                raise AssertionError(role + " could read another principal's private credentials")
            result[role] = {"uid": UIDS[role], "perception_credentials_unreadable": denied,
                            "other_principal_credentials_unreadable": cross_denied}
        return result

    def probe_graph_boundaries(self, base):
        result = {}
        for role, uid in UIDS.items():
            probe = subprocess.run(sandboxed([sys.executable, str(Path(__file__).with_name("role_probe.py")), role]),
                env=self.environment(role, base), user=uid, group=uid, extra_groups=[],
                capture_output=True, text=True, timeout=15, close_fds=True)
            if probe.returncode:
                raise AssertionError("role DDS boundary failed: " + role + " " + probe.stderr[-1500:])
            result[role] = json.loads(probe.stdout.splitlines()[-1])
        return result
