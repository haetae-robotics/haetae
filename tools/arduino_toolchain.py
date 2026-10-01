"""Pinned, repo-local Arduino toolchain. No global installation or implicit upload."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request

CLI_VERSION = "1.5.1"
CORE_VERSION = "1.6.0"
FQBN = "arduino:renesas_uno:minima"
COMPILE_IMAGE = "python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b"
ROOT = Path(__file__).resolve().parents[1]


def setup():
    system = {"Darwin": "macOS", "Linux": "Linux"}.get(platform.system())
    arch = {"arm64": "ARM64", "aarch64": "ARM64", "x86_64": "64bit"}.get(platform.machine())
    if not system or not arch:
        raise RuntimeError("Arduino bench supports macOS/Linux arm64 or x86_64")
    home = ROOT / "artifacts" / f"arduino-{system}-{arch}"
    home.mkdir(parents=True, exist_ok=True)
    binary = home / "arduino-cli"
    if not binary.exists():
        name = f"arduino-cli_{CLI_VERSION}_{system}_{arch}.tar.gz"
        base = f"https://github.com/arduino/arduino-cli/releases/download/v{CLI_VERSION}/"
        checksums = urllib.request.urlopen(base + f"{CLI_VERSION}-checksums.txt", timeout=60).read().decode()
        expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == name)
        archive = home / name
        urllib.request.urlretrieve(base + name, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            archive.unlink()
            raise RuntimeError("Arduino CLI archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.getmember("arduino-cli")
            binary.write_bytes(tar.extractfile(member).read())
        binary.chmod(0o755)
        archive.unlink()
    version = subprocess.check_output([str(binary), "version", "--format", "json"], text=True)
    if json.loads(version)["VersionString"].lstrip("v") != CLI_VERSION:
        raise RuntimeError("unexpected Arduino CLI version; remove artifacts/arduino and retry")
    config = home / "arduino-cli.yaml"
    config.write_text(json.dumps({"directories": {
        "data": str(home / "data"), "downloads": str(home / "downloads"),
        "user": str(home / "user")}}))
    argv = [str(binary), "--config-file", str(config)]
    installed = json.loads(subprocess.check_output(argv + ["core", "list", "--format", "json"], text=True))
    if not any(p["id"] == "arduino:renesas_uno" and p["installed_version"] == CORE_VERSION
               for p in installed.get("platforms", [])):
        subprocess.run(argv + ["core", "update-index"], check=True)
        subprocess.run(argv + ["core", "install", f"arduino:renesas_uno@{CORE_VERSION}"], check=True)
    return argv


def compile_sketch(argv):
    build = ROOT / "artifacts" / "uno-r4-build"
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        compiler = ROOT / "artifacts" / "arduino-macOS-ARM64" / "data" / "packages" / "arduino" / "tools" / "arm-none-eabi-gcc" / "7-2017q4" / "bin" / "arm-none-eabi-g++"
        try:
            subprocess.run([str(compiler), "--version"], check=True, stdout=subprocess.DEVNULL)
        except OSError:
            print("Arduino core compiler requires Rosetta on this Mac; compiling in Linux Docker instead.", flush=True)
            subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64",
                            "-v", str(ROOT) + ":/work", "-w", "/work", COMPILE_IMAGE,
                            "python3", "haetae-bench", "compile"], check=True)
            return build
    subprocess.run(argv + ["compile", "--fqbn", FQBN, "--warnings", "all",
                          "--build-path", str(build), str(ROOT / "hardware" / "uno_r4_bench")], check=True)
    return build


def flash(argv, port):
    # Refuse a detected other board. Unknown identities require fixing detection first.
    boards = json.loads(subprocess.check_output(argv + ["board", "list", "--format", "json"], text=True))
    found = next((p for p in boards.get("detected_ports", []) if p["port"]["address"] == port), None)
    if not found or not any(b.get("fqbn") == FQBN for b in found.get("matching_boards", [])):
        raise RuntimeError("port is not detected as UNO R4 Minima; run ports and check the cable/board")
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        uploader = ROOT / "artifacts" / "arduino-macOS-ARM64" / "data" / "packages" / "arduino" / "tools" / "bossac" / "1.9.1-arduino5" / "bossac"
        try:
            subprocess.run([str(uploader), "--help"], check=True, stdout=subprocess.DEVNULL)
        except OSError as exc:
            raise RuntimeError("UNO R4 uploader requires Rosetta on Apple Silicon. Install Rosetta through macOS, then retry flash. Docker compile does not provide USB upload.") from exc
    build = compile_sketch(argv)
    subprocess.run(argv + ["upload", "--fqbn", FQBN, "--port", port,
                          "--input-dir", str(build)], check=True)
