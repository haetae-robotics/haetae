"""Bounded attack probes for the Gazebo live run.

The secured Gazebo probe uses the live simulator graph and a restricted OS
user. The fallback DDS probe uses a separate graph with a controller stub.
The signed-input probe runs the production Rust enforcer with fresh state.
"""

import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile


from network_guard import sandboxed

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ros/haetae_sim"))
sys.path.insert(0, str(REPO / "ros/haetae_gate"))
from run_scenario import fixture, public  # noqa: E402
from bridge import Bridge  # noqa: E402
from signing import Signer  # noqa: E402


def _checked(argv, env=None, timeout=35, user=None, group=None):
    options = {"user": user, "group": group, "extra_groups": []} if user is not None else {}
    result = subprocess.run(sandboxed(argv) if user is not None else argv, env=env, capture_output=True, text=True,
                            timeout=timeout, close_fds=True, **options)
    if result.returncode:
        raise RuntimeError(f"{' '.join(argv[:3])} failed: {result.stderr[-1200:]}")
    if not result.stdout.strip() and Path(argv[0]).name.startswith("python"):
        raise RuntimeError(f"{' '.join(argv[:3])} produced no result: {result.stderr[-1200:]}")
    return result.stdout


def _last_json(output, description):
    try:
        return next(json.loads(line) for line in reversed(output.splitlines())
                    if line.lstrip().startswith("{"))
    except (StopIteration, json.JSONDecodeError) as exc:
        raise RuntimeError(description + " did not return a result: " + output[-1200:]) from exc


def prepare_gazebo_security(root):
    """Create fresh DDS credentials for the simulator and restricted VLA."""
    root.mkdir(parents=True, exist_ok=True)
    keystore = root / "keystore"
    _checked(["ros2", "security", "create_keystore", str(keystore)])
    policy = REPO / "ros/gazebo/gazebo.policy.xml"
    for enclave in ("/haetae/sim", "/haetae/world", "/haetae/gate", "/haetae/vla", "/haetae/vla_signer"):
        _checked(["ros2", "security", "create_enclave", str(keystore), enclave])
        _checked(["ros2", "security", "create_permission", str(keystore), enclave,
                  str(policy)])
    return keystore


def probe_gazebo_permissions(env):
    if os.geteuid() != 0:
        raise RuntimeError("Gazebo attack isolation requires a root-run test container")
    nobody = pwd.getpwnam("nobody")
    with tempfile.TemporaryDirectory(prefix="haetae-vla-") as directory:
        private = Path(directory)
        keystore = private / "keystore"
        source = Path(env["ROS_SECURITY_KEYSTORE"]) / "enclaves/haetae/vla"
        shutil.copytree(source, keystore / "enclaves/haetae/vla")
        logs = private / "logs"
        logs.mkdir()
        for path in (private, *private.rglob("*")):
            os.chown(path, nobody.pw_uid, nobody.pw_gid)
            path.chmod(0o700 if path.is_dir() else 0o600)
        attacker_env = {**env, "ROS_SECURITY_KEYSTORE": str(keystore),
                        "ROS_LOG_DIR": str(logs)}
        output = _checked([sys.executable, str(REPO / "ros/gazebo/secure_attack.py")],
                          attacker_env, timeout=15, user=nobody.pw_uid,
                          group=nobody.pw_gid)
    result = _last_json(output, "Gazebo SROS2 attacker")
    if (not result.get("allowed_proposal_created") or
            not result.get("allowed_proposal_matched") or
            result.get("uid") != nobody.pw_uid or
            not all(result["attempted"].values())):
        raise AssertionError("Gazebo attacker lacked an authorized route: " +
                             json.dumps(result, separators=(",", ":")))
    return result


def probe_permissions(root):
    """Prove a VLA enclave cannot reach protected topics in an isolated DDS graph."""
    root.mkdir(parents=True, exist_ok=True)
    keystore = root / "keystore"
    env = os.environ.copy()
    env["ROS_DOMAIN_ID"] = "82"
    _checked(["ros2", "security", "create_keystore", str(keystore)], env)
    policy = REPO / "ros/security/haetae.policy.xml"
    for enclave in ("/haetae/gate", "/haetae/world", "/haetae/vla", "/haetae/controller"):
        _checked(["ros2", "security", "create_enclave", str(keystore), enclave], env)
        _checked(["ros2", "security", "create_permission", str(keystore), enclave,
                  str(policy)], env)
    output = _checked([sys.executable, str(REPO / "ros/security/secure_probe.py"),
                       "orchestrate", str(keystore)], env, timeout=45)
    result = _last_json(output, "SROS2 probe")
    if not result.get("ok") or not all(result["authorized"].values()):
        raise AssertionError("authorized DDS control path did not work")
    if any(result["unauthorized_received"].values()):
        raise AssertionError("unauthorized DDS data reached a protected receiver")
    return {"direct_command_blocked": True, "forged_world_blocked": True,
            "scope": "isolated_sros2_reference_graph", "denied": result["vla_denied"],
            "unauthorized_received": result["unauthorized_received"],
            "authorized": result["authorized"]}


def probe_signed_inputs(root, binary):
    """Prove the actual enforcer rejects a replay and a changed signed world."""
    root.mkdir(parents=True, exist_ok=True)
    fixture(root, binary)
    signer = Signer(root / "trust.json", root / "state.json", {
        role: str(root / (role + ".key")) for role in ("world", "fault", "vla")})
    argv = [binary, "enforce", "--stdio", "--policy", str(root / "policy.json"),
            "--state", str(root / "state.json"), "--sillok", str(root / "sillok.jsonl"),
            "--key", str(root / "log.key"), "--trust", str(root / "trust.json"),
            "--root-pubkey", public(1)]
    bridge = Bridge(argv, 500)

    def send(envelope, stamp):
        return bridge.request({"k": "signed", "t": stamp,
                               "data": json.dumps(envelope, separators=(",", ":"))})

    try:
        world = {"stamp_ms": 1000, "robot": {"pose": {"x": 5.0, "y": 5.0},
                                             "yaw": 0.0, "twist": {"linear": 0.0, "angular": 0.0}}, "humans": [], "confidence": 1.0}
        send(signer.sign("world", world), 1000)
        send(signer.sign("vla", {"id": 1, "source": "vla", "timestamp_ms": 1000,
                                 "action": {"type": "stop"}}), 1000)
        move = signer.sign("vla", {"id": 2, "source": "vla", "timestamp_ms": 1000,
                                   "action": {"type": "velocity", "linear": 0.5,
                                              "angular": 0.0, "ttl_ms": 200}})
        accepted = send(move, 1000)
        if accepted["cmd"]["linear"] != 0.5:
            raise AssertionError("positive control: original signed command was not accepted")
        replay = send(move, 1001)
        replay_reason = replay.get("outcome", {}).get("rejected", {}).get("error", "")
        if replay["cmd"]["linear"] != 0 or "replayed" not in replay_reason:
            raise AssertionError("replayed signed command was not rejected")
        person_report = {**world, "humans": [{"id": "sim-person", "class": "adult",
                                               "pos": {"x": 5.3, "y": 5.0}}]}
        forged = signer.sign("world", person_report)
        forged["payload"] = json.dumps(world, separators=(",", ":"))
        rejected = send(forged, 1002)
        forged_reason = rejected.get("outcome", {}).get("rejected", {}).get("error", "")
        if rejected["cmd"]["linear"] != 0 or "signature" not in forged_reason:
            raise AssertionError("forged signed world was not rejected")
        return {"replay_blocked": True, "forged_signature_blocked": True,
                "scope": "isolated_production_enforcer",
                "original_command_mps": accepted["cmd"]["linear"],
                "replay_command_mps": replay["cmd"]["linear"],
                "forged_command_mps": rejected["cmd"]["linear"],
                "replay_reason": replay_reason, "forged_reason": forged_reason}
    finally:
        bridge.close()
