"""Bounded lockstep socket frames; each frame keeps one original deadline."""
import time


def remaining(service, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("permit IPC frame deadline expired")
    service.settimeout(left)


def mark(stages, name):
    if stages is not None:
        stages[name] = time.monotonic_ns()


def send_line(service, raw, deadline, stages=None, label="response"):
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or len(raw) > 513:
        raise ValueError("invalid permit IPC frame")
    mark(stages, label + "_send_started_ns")
    remaining(service, deadline)
    service.sendall(raw)
    remaining(service, deadline)
    mark(stages, label + "_sent_ns")


def receive_line(service, deadline, stages=None, label="response"):
    raw = bytearray()
    while True:
        remaining(service, deadline)
        chunk = service.recv(513 - len(raw))
        remaining(service, deadline)
        if not chunk:
            raise RuntimeError("permit IPC closed before complete frame")
        if not raw:
            mark(stages, label + "_first_byte_ns")
        raw.extend(chunk)
        if b"\n" in raw:
            if not raw.endswith(b"\n") or b"\n" in raw[:-1]:
                raise ValueError("permit IPC trailing frame bytes")
            mark(stages, label + "_received_ns")
            return bytes(raw)
        if len(raw) == 513:
            raise ValueError("oversized permit IPC frame")
