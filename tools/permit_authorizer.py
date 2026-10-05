"""Trusted single operator session. Gateway requests are never gate decisions."""
import json
import hashlib
from pathlib import Path
import socket
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from bench_gate import BenchGate
from permit_keys import deployment, private_bytes
from permit_protocol import context, decode_json, payload, bind_payload


class Authorizer:
    def __init__(self, binary, key_path, deployment_path, scenario, seconds):
        if scenario not in ("allow", "person", "world-loss", "replay", "invalid-signature") or not 1 <= seconds <= 30:
            raise ValueError("invalid explicit operator session")
        self.config = deployment(deployment_path)
        self.key = Ed25519PrivateKey.from_private_bytes(private_bytes(key_path))
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        if public != self.config["public_key"]:
            raise ValueError("authorizer key does not match deployment")
        self.gate = BenchGate(binary)
        self.scenario, self.seconds = scenario, seconds
        self.started = None
        self.binding = None
        self.sequence = self.challenge = 0
        self.locked = False
        self.last_fault = None
        self.session_key = None
        self.armed_authorized = False

    def approve(self, request):
        try:
            context(request)
            binding = request["epoch"], request["generation"]
            if self.locked or request["install"] != self.config["install"]:
                raise ValueError("authorizer locked or wrong installation")
            if self.binding is None:
                if request["op"] != "BIND" or request["sequence"] != 0:
                    raise ValueError("explicit session must begin with signed OFF-only BIND")
                self.binding = binding
                ephemeral_key = X25519PrivateKey.generate()
                ephemeral = ephemeral_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
                bytes_ = bind_payload(request, ephemeral, self.config["controller_public_key"])
                shared = ephemeral_key.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(self.config["controller_public_key"])))
                self.session_key = hashlib.blake2b(bytes_, key=shared, digest_size=32).digest()
                signature = self.key.sign(bytes_).hex()
                self.sequence, self.challenge = 1, request["challenge"]
                return {"kind": "permit", "signature": signature, "ephemeral": ephemeral,
                        "duration": 0, "remaining_ms": 500}
            elif binding != self.binding or request["op"] != ("RUN" if self.armed_authorized else "ARM"):
                raise ValueError("controller session changed; new operator invocation required")
            if request["sequence"] != self.sequence or request["challenge"] <= self.challenge:
                raise ValueError("permit request replay/order failure")
            if self.started is None:
                self.started = time.monotonic()
            elapsed = time.monotonic() - self.started
            fault = self.scenario if elapsed >= self.seconds and self.scenario != "allow" else None
            if self.scenario == "allow" and elapsed >= self.seconds:
                self.locked = True
                return {"kind": "terminal", "reason": "operator_complete"}
            if fault and fault != self.last_fault:
                self.last_fault = fault
                print("Authorizer fault injected: " + fault, flush=True)
            if not self.gate.cycle(fault):
                self.locked = True
                return {"kind": "terminal", "reason": "gate_denied"}
            signature = hashlib.blake2b(payload(request), key=self.session_key, digest_size=32).hexdigest()
            # Signing cannot turn a late gate decision into fresh authorization.
            remaining = self.gate.remaining()
            if not 0 < remaining <= .05:
                self.locked = True
                return {"kind": "terminal", "reason": "gate_expired"}
            self.sequence, self.challenge = request["sequence"] + 1, request["challenge"]
            self.armed_authorized = True
            return {"kind": "permit", "signature": signature, "duration": 200,
                    "remaining_ms": remaining * 1000}
        except (RuntimeError, ValueError):
            self.locked = True
            return {"kind": "terminal", "reason": "authorizer_failure"}

    def close(self):
        self.gate.close()


def serve(binary, key, config, path, scenario, seconds, ready_file=None, socket_group=None):
    """Parent directory must be provisioned by the trusted operator, not relay."""
    import os
    if Path(path).exists():
        raise ValueError("authorizer socket already exists; no automatic replacement")
    authorizer = Authorizer(binary, key, config, scenario, seconds)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        if socket_group is not None:
            os.chown(path, -1, socket_group)
        os.chmod(path, 0o660 if socket_group is not None else 0o600)
        server.listen(1)
        server.settimeout(5)
        if ready_file:
            Path(ready_file).write_text(json.dumps({"ready": True}))
        connection, _ = server.accept()
        with connection:
            connection.settimeout(.5)
            stream = connection.makefile("rwb", buffering=0)
            deadline = time.monotonic() + seconds + 3
            while time.monotonic() < deadline:
                raw = stream.readline(513)
                if not raw:
                    return
                if not raw.endswith(b"\n"):
                    return
                result = authorizer.approve(decode_json(raw))
                stream.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")
                if result["kind"] == "terminal":
                    print("Authorizer terminal: " + result["reason"], flush=True)
                    return
    finally:
        authorizer.close()
        server.close()
        Path(path).unlink(missing_ok=True)
