"""Bind a developer run to clean software and physical USB qualification."""
import hashlib
import json
from pathlib import Path
import subprocess


def require_verified_h2(root, binary, directory):
    root, binary, directory = Path(root), Path(binary), Path(directory)
    try:
        software = json.loads((root / "artifacts/controller-permit/report.json").read_text())
        usb = json.loads((root / "artifacts/controller-permit-usb/report.json").read_text())
        manifest = json.loads((root / "artifacts/controller-permit-build/manifest.json").read_text())
        config = json.loads((directory / "deployment.json").read_text())
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
        sources = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((root / "hardware/uno_r4_permit").rglob("*")) if p.is_file()}
        image = root / "artifacts/controller-permit-build/build/uno_r4_permit.ino.bin"
        sanitized = {k: v for k, v in manifest.items() if k not in ("install", "public_key")}
        valid = (not dirty and software["passed"] is True and usb["passed"] is True and
                 software["source_revision"] == usb["source_revision"] == revision and
                 software["source_dirty"] is False and usb["source_dirty"] is False and
                 software["firmware_sha256"] == usb["firmware_source_sha256"] == sources and
                 software["gate_sha256"] == usb["gate_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest() and
                 usb["build_manifest"] == sanitized and manifest["firmware_source_sha256"] == sources and
                 manifest["resolved_core"] == "haetae_permit" and
                 all(manifest[k] == config[k] for k in ("install", "public_key", "controller_public_key")) and
                 manifest["binary_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("run H2 verify, compile/flash and verify-usb on a clean checkout before run") from exc
    if not valid:
        raise RuntimeError("H2 verification snapshot changed; repeat verify and verify-usb on a clean checkout")
    return software, usb
