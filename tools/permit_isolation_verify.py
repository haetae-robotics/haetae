"""Linux supervisor verifies actual authorizer/gateway/AI UID separation."""
import json
import hashlib
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile

from bench_verify import wait_for
from permit_checks import require
from permit_keys import provision
from permit_verify import ROOT, OUT, Device, compile_native


def role(uid, groups=()):
    def drop():
        os.setgroups(list(groups))
        os.setgid(uid)
        os.setuid(uid)
    return drop


def stage_public_runtime(root, binary, destination):
    """Do not make a private checkout/home traversable to untrusted UIDs."""
    destination.mkdir(mode=0o755)
    destination.chmod(0o755)
    entry = destination / "haetae-permit"
    shutil.copyfile(root / "haetae-permit", entry)
    entry.chmod(0o555)
    for folder in ("tools", "ros/haetae_gate"):
        target = destination / folder
        target.mkdir(mode=0o755, parents=True)
        target.chmod(0o755)
        if folder.startswith("ros/"):
            (destination / "ros").chmod(0o755)
        for source in (root / folder).glob("*.py"):
            shutil.copyfile(source, target / source.name)
            (target / source.name).chmod(0o444)
    gate = destination / "gate"
    shutil.copyfile(binary, gate)
    gate.chmod(0o555)
    if hashlib.sha256(gate.read_bytes()).digest() != hashlib.sha256(binary.read_bytes()).digest():
        raise RuntimeError("staged gate binary changed")
    return entry, gate


def main(binary=None):
    if sys.platform != "linux" or os.getuid() != 0:
        raise RuntimeError("isolation verification requires a Linux root supervisor; use the documented Docker/CI command")
    compile_native()
    binary = Path(binary or ROOT / "target/release/haetae").resolve()
    report = {"kind": "Linux-UID-controller-permit-bench", "passed": False,
              "host_root_trusted": True, "physical_hardware_tested": False, "roles": {"authorizer": 2001, "gateway": 2003, "AI": 2004}}
    report["gate_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    report["source_revision"] = subprocess.check_output(["git", "-c", "safe.directory=" + str(ROOT),
                                                       "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # All private source fixture state is in tmpfs in CI/qualification.
    base = "/dev/shm" if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="haetae-permit-isolation-", dir=base) as tmp:
        tmp = Path(tmp)
        tmp.chmod(0o755)
        entry, staged_binary = stage_public_runtime(ROOT, binary, tmp / "public-runtime")
        private = tmp / "private"
        config = provision(private)
        for child in private.iterdir():
            os.chown(child, 2001, 2001)
        os.chown(private, 2001, 2001)
        public = tmp / "deployment.json"
        public.write_text(json.dumps(config))
        ipc = tmp / "ipc"
        ipc.mkdir(mode=0o750)
        os.chown(ipc, 2001, 2003)
        scratch = tmp / "authorizer-tmp"
        scratch.mkdir(mode=0o700)
        os.chown(scratch, 2001, 2001)
        relay_scratch = tmp / "relay-tmp"
        relay_scratch.mkdir(mode=0o700)
        os.chown(relay_scratch, 2003, 2003)
        device = Device(config, private / "controller.seed", "uid-isolation")
        os.chown(device.port, 2003, 2003)
        os.chmod(device.port, 0o600)
        authorizer = relay = None
        try:
            with (OUT / "isolation-authorizer.log").open("w") as auth_log, (OUT / "isolation-relay.log").open("w") as relay_log:
                authorizer = subprocess.Popen([sys.executable, str(entry), "authorizer",
                    "--deployment", str(public), "--key", str(private / "authorizer.seed"), "--socket", str(ipc / "auth.sock"),
                    "--socket-group", "2003", "--seconds", "1", "--scenario", "person", "--ready-file", str(ipc / "ready"),
                    "--binary", str(staged_binary)],
                    preexec_fn=role(2001, [2003]), env=dict(os.environ, TMPDIR=str(scratch)),
                    stdout=auth_log, stderr=auth_log, start_new_session=True)
                def ready():
                    if authorizer.poll() is not None:
                        raise AssertionError("isolated authorizer failed; see isolation-authorizer.log")
                    return (ipc / "ready").exists()
                wait_for(ready, 5)
                probe = """import json,os,sys
results={}
for name in ['authorizer.seed','controller.seed']:
 try:
  with open(sys.argv[1]+'/'+name,'rb') as f: f.read(1)
  results[name+'_read_denied']=False
 except PermissionError: results[name+'_read_denied']=True
try:
 os.kill(int(sys.argv[2]),0); results['authorizer_signal_denied']=False
except PermissionError: results['authorizer_signal_denied']=True
try:
 fd=os.open(sys.argv[1]+'/authorizer.seed',os.O_WRONLY);os.close(fd);results['key_write_denied']=False
except PermissionError: results['key_write_denied']=True
print(json.dumps(results))
"""
                report["denials"] = {}
                for uid in (2003, 2004):
                    result = subprocess.run([sys.executable, "-c", probe, str(private), str(authorizer.pid)],
                                            preexec_fn=role(uid), capture_output=True, check=True, text=True)
                    denied = json.loads(result.stdout)
                    require(all(denied.values()), 'role separation failed')
                    report["denials"][str(uid)] = denied
                relay_ready = relay_scratch / "ready"
                relay = subprocess.Popen([sys.executable, str(entry), "relay", "--port", device.port,
                    "--deployment", str(public), "--authorizer-socket", str(ipc / "auth.sock"), "--ready-file", str(relay_ready)],
                    preexec_fn=role(2003), stdout=relay_log, stderr=relay_log)
                def positive():
                    if relay.poll() is not None:
                        raise AssertionError("isolated relay failed; see isolation-relay.log")
                    return relay_ready.exists() and device.on()
                on = wait_for(positive)
                require(relay.wait(timeout=4) == 0, 'permit_isolation_verify.py: qualification predicate failed')
                require('Authorizer fault injected: person' in (OUT / 'isolation-authorizer.log').read_text(), 'permit_isolation_verify.py: qualification predicate failed')
                stopped = wait_for(lambda: device.stopped(on["device_ms"], "stop"))
                report["positive_controller_ON"] = True
                report["gate_hazard_to_controller_LOCKED"] = bool(stopped)
                report["passed"] = True
        finally:
            for child in (relay, authorizer):
                if child and child.poll() is None:
                    child.kill()
                if child:
                    child.wait(timeout=2)
            if authorizer:
                try:
                    os.killpg(authorizer.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            device.close()
            (OUT / "isolation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Linux UID separation and actual signed controller ON/hazard LOCKED passed.")
