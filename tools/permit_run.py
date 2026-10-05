"""Convenient explicit developer bench; same UID is a trusted host profile."""
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from permit_keys import deployment
from permit_link import relay
from permit_provenance import require_verified_h2

ROOT = Path(__file__).resolve().parents[1]


def run(port, directory, scenario, seconds):
    directory = Path(directory).resolve()
    config = deployment(directory / "deployment.json")
    require_verified_h2(ROOT, ROOT / "target/release/haetae", directory)
    with tempfile.TemporaryDirectory(prefix="h2-") as tmp:
        tmp = Path(tmp)
        socket, ready = tmp / "auth.sock", tmp / "ready"
        child = subprocess.Popen([sys.executable, str(ROOT / "haetae-permit"), "authorizer",
            "--deployment", str(directory / "deployment.json"), "--key", str(directory / "authorizer.seed"),
            "--socket", str(socket), "--scenario", scenario, "--seconds", str(seconds), "--ready-file", str(ready)],
            start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists():
                if child.poll() is not None:
                    raise RuntimeError("authorizer failed before startup")
                if time.monotonic() >= deadline:
                    raise RuntimeError("authorizer startup timed out while LED OFF")
                time.sleep(.005)
            relay(port, config["install"], socket)
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=2)
            try:
                import os
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
