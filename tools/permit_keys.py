"""Explicit device provisioning. All private material stays out of git/reports."""
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def private_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 32):
            raise ValueError("private seed must be owner-only, regular, single-link, 32 bytes")
        raw = os.read(fd, 33)
        if len(raw) != 32:
            raise ValueError("invalid private seed")
        return raw
    finally:
        os.close(fd)


def deployment(path):
    data = json.loads(Path(path).read_text())
    if (not isinstance(data, dict) or set(data) != {"schema", "install", "public_key", "controller_public_key"} or
            type(data["schema"]) is not int or data["schema"] != 2 or
            not isinstance(data["install"], str) or not re.fullmatch(r"[0-9a-f]{32}", data["install"]) or
            not isinstance(data["public_key"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["public_key"]) or
            data["public_key"] == "00" * 32 or not isinstance(data["controller_public_key"], str) or
            not re.fullmatch(r"[0-9a-f]{64}", data["controller_public_key"]) or data["controller_public_key"] == "00"*32):
        raise ValueError("invalid public deployment metadata")
    return data


def provision(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Newly created directory only; no implicit key overwrite/rotation.
    os.chmod(directory, 0o700)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    controller_seed = secrets.token_bytes(32)
    for name, raw in (("authorizer.seed", key.private_bytes(serialization.Encoding.Raw,
                       serialization.PrivateFormat.Raw, serialization.NoEncryption())),
                      ("controller.seed", controller_seed)):
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            if os.write(fd, raw) != len(raw):
                raise OSError("short private seed write")
            os.fsync(fd)
        finally:
            os.close(fd)
    controller_private = X25519PrivateKey.from_private_bytes(
        hashlib.blake2b(b"HAETAE-X25519-H2", key=controller_seed, digest_size=32).digest())
    controller_public = controller_private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    result = {"schema": 2, "install": secrets.token_hex(16), "public_key": public.hex(), "controller_public_key": controller_public.hex()}
    (directory / "deployment.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
