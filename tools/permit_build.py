"""Device-specific hardened core copy; stock installation is never patched."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

import arduino_toolchain as arduino
from permit_keys import deployment, private_bytes

ROOT = Path(__file__).resolve().parents[1]
FQBN = "arduino:renesas_uno:haetae_minima"
CORE_HASHES = {
    "usb/USB.cpp": "903c7bbeaccb80e4b6abf8ef55f7fef1fb030588464284b28ec595b1ea772614",
    "usb/SerialUSB.cpp": "ce16baaf3a895ac6a111632dc940da272223e8c97dfab9cdbff01e1056e116b0",
}


def replace_function(source, declaration):
    if source.count(declaration) != 1:
        raise ValueError("unexpected pinned core function")
    start = source.index("{", source.index(declaration))
    depth, end = 1, start + 1
    # The two pinned upstream functions contain no braces in string literals.
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[:start] + "{ /* Haetae runtime programming entry disabled. */ }" + source[end:]


def harden_core(stock, destination):
    for relative, expected in CORE_HASHES.items():
        if hashlib.sha256((stock / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("pinned core differs; review runtime USB hardening before compiling")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(stock, destination)
    usb = destination / "usb/USB.cpp"
    source = usb.read_text()
    if source.count("install_DFU = __USBInstallSerial;") != 1:
        raise ValueError("unexpected DFU descriptor installation")
    usb.write_text(source.replace("install_DFU = __USBInstallSerial;", "install_DFU = false; // Haetae H2 runtime profile"))
    serial = destination / "usb/SerialUSB.cpp"
    source = replace_function(serial.read_text(), "static void CheckSerialReset()")
    source = replace_function(source, 'extern "C" void tud_dfu_runtime_reboot_to_dfu_cb(void)')
    serial.write_text(source)
    return {relative: hashlib.sha256((destination / relative).read_bytes()).hexdigest() for relative in CORE_HASHES}


def profile_board(installed):
    # --build-property build.core changes the display property after the CLI
    # already resolves the original core. A distinct board mapping is required.
    lines = [line.replace("minima.", "haetae_minima.", 1) for line in (installed / "boards.txt").read_text().splitlines()
             if line.startswith("minima.")]
    if not lines or sum(line.startswith("haetae_minima.build.core=") for line in lines) != 1:
        raise ValueError("missing pinned Minima board mapping")
    lines = ["haetae_minima.build.core=haetae_permit" if line.startswith("haetae_minima.build.core=") else
             "haetae_minima.name=Haetae UNO R4 Minima runtime profile" if line.startswith("haetae_minima.name=") else line
             for line in lines]
    content = "# Generated Haetae H2 mapping; original Minima mapping unchanged.\n" + "\n".join(lines) + "\n"
    local = installed / "boards.local.txt"
    if local.exists() and local.read_text() != content:
        raise ValueError("existing local board overrides differ; review before rebuilding")
    local.write_text(content)
    return hashlib.sha256(content.encode()).hexdigest()


def build(config_path, controller_key, port=None):
    config = deployment(config_path)
    nonce_key = private_bytes(controller_key)
    if nonce_key == bytes(32):
        raise ValueError("zero controller challenge key")
    private_key = X25519PrivateKey.from_private_bytes(hashlib.blake2b(b"HAETAE-X25519-H2", key=nonce_key, digest_size=32).digest())
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    if public_key != config["controller_public_key"]:
        raise ValueError("controller seed does not match public deployment")
    # Vendored dependency must remain identical to its reviewed upstream bytes.
    vendor = ROOT / "hardware/uno_r4_permit/src/vendor"
    upstream = json.loads((vendor / "upstream.json").read_text())
    for name, metadata in upstream["files"].items():
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != metadata["sha256"]:
            raise ValueError("vendored Monocypher source checksum changed")
    argv = arduino.setup()
    installed = Path(argv[2]).parent / "data/packages/arduino/hardware/renesas_uno" / arduino.CORE_VERSION
    hardened = installed / "cores/haetae_permit"
    hashes = harden_core(installed / "cores/arduino", hardened)
    board_hash = profile_board(installed)
    private = ROOT / "artifacts/controller-permit-build"
    private.mkdir(mode=0o700, exist_ok=True)
    os.chmod(private, 0o700)
    stage = private / "uno_r4_permit"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(ROOT / "hardware/uno_r4_permit", stage)
    header = "#pragma once\n#include <stdint.h>\n"
    for name, value in (("INSTALL", bytes.fromhex(config["install"])),
                        ("PUBLIC_KEY", bytes.fromhex(config["public_key"])), ("CHALLENGE_KEY", nonce_key)):
        header += "static const uint8_t HAETAE_" + name + "[] = {" + ",".join(str(b) for b in value) + "};\n"
    target = stage / "provision.h"
    target.write_text(header)
    target.chmod(0o600)
    output = private / "build"
    properties = subprocess.check_output(argv + ["compile", "--fqbn", FQBN, "--show-properties=expanded", str(stage)], text=True)
    resolved = next((line.split("=", 1)[1] for line in properties.splitlines() if line.startswith("build.core.path=")), None)
    if resolved is None or Path(resolved).resolve() != hardened.resolve():
        raise ValueError("Arduino CLI did not resolve the hardened core; refusing build")
    subprocess.run(argv + ["compile", "--fqbn", FQBN, "--warnings", "all", "--clean",
                          "--build-path", str(output), str(stage)], check=True)
    manifest = {"schema": 2, "scope": "device-specific LED firmware; private image, not redistributable",
                "install": config["install"], "public_key": config["public_key"],
                "arduino_cli": arduino.CLI_VERSION, "arduino_core": arduino.CORE_VERSION,
                "fqbn": FQBN, "resolved_core": "haetae_permit", "board_mapping_sha256": board_hash,
                "stock_core_sha256": CORE_HASHES, "hardened_core_sha256": hashes,
                "runtime_dfu_descriptor": False, "runtime_dfu_detach_reboot": False,
                "runtime_1200_baud_reset": False, "secure_boot_claimed": False,
                "binary_sha256": hashlib.sha256((output / "uno_r4_permit.ino.bin").read_bytes()).hexdigest()}
    manifest["firmware_source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in sorted((ROOT / "hardware/uno_r4_permit").rglob("*")) if p.is_file()}
    (private / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if port:
        detected = json.loads(subprocess.check_output(argv + ["board", "list", "--format", "json"], text=True))
        found = next((p for p in detected.get("detected_ports", []) if p["port"]["address"] == port), None)
        if not found or not any(b.get("fqbn") == arduino.FQBN for b in found.get("matching_boards", [])):
            raise ValueError("upload port is not a detected UNO R4 Minima")
        subprocess.run(argv + ["upload", "--fqbn", FQBN, "--port", port, "--input-dir", str(output)], check=True)
        print("H2 uploaded. Future maintenance needs physical bootloader entry; runtime DFU is disabled.")
    return manifest
