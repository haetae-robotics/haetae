#!/usr/bin/env python3
"""Package committed source plus an allowlisted passing simulator report.

Byte reproducibility covers this archive for identical source/evidence inputs,
not the Docker image or floating Ubuntu/ROS packages. No signing claim.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ros/gazebo"))
from public_report import report  # noqa: E402


def package(result_path, out, household_result=None):
    if household_result is None:
        raise ValueError("alpha packaging requires both reference and household evidence")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=REPO, text=True).strip()
    evidence = result_path.read_bytes()
    raw = json.loads(evidence)
    if not isinstance(raw, dict) or raw.get("source_revision") != revision:
        raise ValueError("evidence source revision must match the packaged commit")
    public = report(raw, revision, "ci")
    if public["status"] != "passed":
        raise ValueError("alpha packaging requires every simulator check, including compound faults, to pass")
    rows = raw["compound_faults"]["iterations"]
    rates = Counter(row.get("proposal_hz_requested") for row in rows)
    if len(rows) < 6 or any(rates[rate] < 2 for rate in (40, 80, 100)):
        raise ValueError("alpha candidate requires six compound iterations, twice at each 40/80/100 Hz rate")
    public_bytes = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode()
    household_bytes = None
    household_evidence = None
    if household_result is not None:
        household_evidence = household_result.read_bytes()
        household_raw = json.loads(household_evidence)
        if not isinstance(household_raw, dict) or household_raw.get("source_revision") != revision or household_raw.get("profile") != "household_hazards":
            raise ValueError("household evidence must match the packaged commit and profile")
        household_public = report(household_raw, revision, "ci_household")
        if household_public["status"] != "passed":
            raise ValueError("household evidence must pass all six cases, controls and durable history")
        household_bytes = (json.dumps(household_public, ensure_ascii=False, indent=2) + "\n").encode()
    prefix = "haetae-simulator-alpha/"
    source = subprocess.check_output(["git", "archive", "--format=tar", "--prefix=" + prefix, revision], cwd=REPO)
    archive = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:") as original, \
            tarfile.open(fileobj=archive, mode="w:", format=tarfile.PAX_FORMAT) as target:
        for member in original.getmembers():
            relative = Path(member.name).relative_to(prefix.rstrip("/"))
            if any(part in (".git", ".omx", "artifacts") for part in relative.parts) or relative.suffix in (".seed", ".key", ".pem"):
                raise ValueError("credential or non-source artifact in tracked archive: " + member.name)
            target.addfile(member, original.extractfile(member) if member.isfile() else None)
        reports = [("REVISION", (revision + "\n").encode()), ("verification-report.json", public_bytes)]
        if household_bytes is not None:
            reports.append(("household-verification-report.json", household_bytes))
        for name, body in reports:
            member = tarfile.TarInfo(prefix + name)
            member.size, member.mode, member.mtime = len(body), 0o644, 0
            target.addfile(member, io.BytesIO(body))
    out.mkdir(parents=True, exist_ok=True)
    artifact = out / "haetae-simulator-alpha.tar.gz"
    with artifact.open("wb") as destination, gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as compressed:
        compressed.write(archive.getvalue())
    (out / "verification-report.json").write_bytes(public_bytes)
    if household_bytes is not None:
        (out / "household-verification-report.json").write_bytes(household_bytes)
    manifest = {"schema_version": 1, "scope": "simulator_evaluation_only", "source_revision": revision,
                "source_tree": tree, "input_evidence_sha256": hashlib.sha256(evidence).hexdigest(),
                "archive_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "report_sha256": hashlib.sha256(public_bytes).hexdigest(),
                "notice": "Unsigned source/evidence candidate; Docker/ROS package resolution is not frozen."}
    if household_bytes is not None:
        manifest["household_input_evidence_sha256"] = hashlib.sha256(household_evidence).hexdigest()
        manifest["household_report_sha256"] = hashlib.sha256(household_bytes).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "SHA256SUMS").write_text("".join(hashlib.sha256((out / name).read_bytes()).hexdigest() + "  " + name + "\n"
        for name in [artifact.name, "verification-report.json", "manifest.json"] + (["household-verification-report.json"] if household_bytes is not None else [])))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--household-result", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.result, args.out, args.household_result)))
