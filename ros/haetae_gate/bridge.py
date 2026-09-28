"""Bounded JSONL request/response bridge. No ROS imports for local tests."""

import json
import math
import os
import select
import subprocess
import threading
import time


class BridgeFailure(RuntimeError):
    pass


def require_fresh_actuation(step, elapsed_ms, now_ms, world_max_age_ms, max_response_ms):
    """Reject a delayed positive result before it can reset a controller deadman."""
    cmd = step["cmd"]
    arm = step.get("arm")
    actuating = cmd["linear"] != 0 or cmd["angular"] != 0 or (
        isinstance(arm, dict) and "execute" in arm)
    if not actuating:
        return
    status = step.get("status") or {}
    expires = status.get("active_expires_ms")
    world_age = status.get("world_age_ms")
    if (type(expires) is not int or type(world_age) is not int
            or elapsed_ms < 0 or elapsed_ms >= max_response_ms
            or now_ms >= expires or world_age + elapsed_ms >= world_max_age_ms):
        raise BridgeFailure("stale actuation response")


class Bridge:
    def __init__(self, argv, timeout_ms=500):
        self.child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, bufsize=0)
        self.timeout = timeout_ms / 1000.0
        self.buffer = bytearray()
        self.lock = threading.Lock()

    def request(self, message):
        raw = json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        if len(raw) > 1024 * 1024:
            raise BridgeFailure("request exceeds 1 MiB")
        with self.lock:
            if self.child.poll() is not None:
                raise BridgeFailure("gate process exited")
            try:
                self.child.stdin.write(raw)
                self.child.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise BridgeFailure("gate input closed") from exc
            deadline = time.monotonic() + self.timeout
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeFailure("gate response timeout")
                readable, _, _ = select.select([self.child.stdout], [], [], remaining)
                if not readable:
                    raise BridgeFailure("gate response timeout")
                chunk = os.read(self.child.stdout.fileno(), 65536)
                if not chunk:
                    raise BridgeFailure("gate output closed")
                self.buffer.extend(chunk)
                if len(self.buffer) > 1024 * 1024:
                    raise BridgeFailure("gate response exceeds 1 MiB")
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                answer = json.loads(line)
                cmd = answer["cmd"]
                if any(isinstance(cmd[key], bool) or not isinstance(cmd[key], (int, float))
                       or not math.isfinite(cmd[key]) for key in ("linear", "angular")):
                    raise ValueError("invalid velocity response")
                return answer
            except (ValueError, KeyError, TypeError) as exc:
                raise BridgeFailure("malformed gate response") from exc

    def close(self):
        if self.child.poll() is None:
            self.child.kill()
            self.child.wait(timeout=2)
        if self.child.stdin:
            self.child.stdin.close()
        if self.child.stdout:
            self.child.stdout.close()
