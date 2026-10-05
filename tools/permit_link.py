"""Untrusted relay: it holds no signing or controller challenge private key."""
import json
import os
from pathlib import Path
import re
import select
import socket
import time
from collections import deque

from bench_link import LinkError, SerialLink
from permit_protocol import context, frame
from permit_ipc import send_line, receive_line


class PermitLink(SerialLink):
    def __init__(self, port, install):
        super().__init__(port)
        self.install = install
        self.current = None

    def wire(self, raw, timeout=.050):
        deadline = time.monotonic() + timeout
        while raw:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [self.fd], [], left)[1]:
                raise LinkError("H2 USB write timeout")
            count = os.write(self.fd, raw)
            if count <= 0:
                raise LinkError("H2 USB closed")
            raw = raw[count:]
        buffer = bytearray()
        while b"\n" not in buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                raise LinkError("H2 USB response timeout")
            chunk = os.read(self.fd, 160)
            if not chunk:
                raise LinkError("H2 USB closed")
            buffer.extend(chunk)
            if len(buffer) > 160:
                raise LinkError("oversized H2 response")
        line, _, rest = buffer.partition(b"\n")
        fields = line.decode("ascii").split(" ")
        if (rest or len(fields) != 11 or fields[:3] != ["H2", "OK", self.install] or
                any(not re.fullmatch(r"0|[1-9][0-9]{0,9}", f) for f in fields[3:8]) or
                not re.fullmatch(r"[0-9a-f]{32}", fields[8]) or fields[9] not in ("LOCKED", "BOUND", "ARMED", "ON")):
            raise LinkError("invalid H2 controller response: " + line.decode("ascii", errors="replace"))
        value = dict(zip(("epoch", "generation", "sequence", "challenge", "issued"), map(int, fields[3:8])))
        value.update(install=self.install, nonce=fields[8], op="RUN")
        context(value)
        self.current = value
        return fields[9]

    def query(self, op):
        if op not in ("HELLO", "STATUS", "STOP"):
            raise ValueError("unsigned command cannot actuate")
        previous = self.current
        state = self.wire(f"H2 {op}\n".encode())
        if op == "HELLO" and (state != "LOCKED" or self.current["sequence"] != 0):
            raise LinkError("HELLO did not lock/reset sequence")
        if op == "STOP" and state != "LOCKED":
            raise LinkError("STOP did not lock controller")
        if previous and op != "HELLO":
            for name in ("epoch", "generation", "sequence"):
                if self.current[name] != previous[name]:
                    raise LinkError("controller context changed")
        return state

    def execute(self, request, permit, timeout):
        binding = request["op"] == "BIND"
        required = {"kind", "signature", "duration", "remaining_ms"} | ({"ephemeral"} if binding else set())
        if (set(permit) != required or permit["kind"] != "permit" or
                permit["duration"] != (0 if binding else 200)):
            raise LinkError("invalid authorizer permit")
        state = self.wire(frame(request, permit["signature"], permit["duration"], permit.get("ephemeral")), timeout)
        if state != {"BIND": "BOUND", "ARM": "ARMED", "RUN": "ON"}[request["op"]]:
            raise LinkError("incorrect authenticated controller state")
        if (self.current["sequence"] != request["sequence"] + 1 or
                self.current["challenge"] != request["challenge"] + 1 or
                any(self.current[k] != request[k] for k in ("epoch", "generation"))):
            raise LinkError("incorrect controller permit acknowledgment")

    def stop(self):
        try:
            self.query("STOP")
        except (RuntimeError, OSError, ValueError):
            pass


def relay(port, install, authorizer_socket, ready_file=None):
    link = PermitLink(port, install)
    service = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    traces = deque(maxlen=4)
    try:
        # Authorizer is already bootstrapped before the short-lived MCU challenge.
        time.sleep(.5)
        service.connect(str(authorizer_socket))
        link.query("HELLO")
        op = "BIND"
        positive = False
        while True:
            if op == "RUN" and link.query("STATUS") not in ("ARMED", "ON"):
                raise LinkError("controller locked; explicit operator restart required")
            request = dict(link.current, op=op)
            started = time.monotonic()
            budget = .500 if op == "BIND" else .050
            stages = {"op": op, "started_ns": time.monotonic_ns()}
            traces.append(stages)
            deadline = started + budget
            send_line(service, json.dumps(request, separators=(",", ":")).encode() + b"\n",
                      deadline, stages, "request")
            raw = receive_line(service, deadline, stages)
            permit = json.loads(raw)
            if permit.get("kind") != "permit":
                link.query("STOP")
                print("차단: 서명 허가 중단, LED OFF", flush=True)
                return
            left = min(budget - (time.monotonic() - started), permit["remaining_ms"] / 1000)
            if left <= 0:
                raise LinkError("authorizer permit arrived late")
            link.execute(request, permit, left)
            stages["controller_ack_ns"] = time.monotonic_ns()
            if time.monotonic() - started >= budget:
                raise LinkError("late controller acknowledgment")
            if op == "RUN" and not positive:
                positive = True
                print("허용: 보드가 서명을 확인함, LED ON", flush=True)
                if ready_file:
                    Path(ready_file).write_text(json.dumps({"on": True}))
            if op == "BIND":
                op = "ARM"
                continue  # No output and no inter-cycle sleep during bootstrap.
            op = "RUN"
            time.sleep(.025)
    except (RuntimeError, OSError, ValueError):
        print(json.dumps({"event": "permit_ipc_failure", "stages": list(traces)}), flush=True)
        raise
    finally:
        link.stop()
        link.close()
        service.close()
