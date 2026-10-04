"""Bind an operator bench run to its completed software verification snapshot."""
import hashlib
import json
from pathlib import Path
import subprocess


def require_verified_binary(root, binary):
    root, binary = Path(root), Path(binary)
    try:
        report = json.loads((root / 'artifacts/bench/report.json').read_text())
        if not isinstance(report, dict):
            raise ValueError('invalid verification report')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip()
        firmware = sorted((root / 'hardware').rglob('*.h')) + sorted((root / 'hardware').rglob('*.ino'))
        hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in firmware}
        valid = (report.get('passed') is True and report.get('source_revision') == revision and
                 report.get('source_dirty') is False and not dirty and
                 report.get('firmware_sha256') == hashes and
                 report.get('gate_sha256') == hashlib.sha256(binary.read_bytes()).hexdigest())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise RuntimeError('run ./haetae-bench verify on a clean checkout before bench run') from exc
    if not valid:
        raise RuntimeError('verification snapshot or binary changed; run ./haetae-bench verify on a clean checkout')
    return report
