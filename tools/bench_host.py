"""LED session: actual Rust approval -> USB RUN, denial/error -> latched STOP."""
import json
from pathlib import Path
import signal
import time

from bench_gate import BenchGate
from bench_link import SerialLink


def run(binary, port, scenario, seconds, ready_file=None):
    if not 1 <= seconds <= 30:
        raise ValueError("seconds must be between 1 and 30")
    if scenario not in ("allow", "person", "world-loss", "replay", "invalid-signature"):
        raise ValueError("unknown bench scenario")
    gate = None
    link = None
    previous = {}

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, interrupted)
    try:
        gate = BenchGate(binary)
        link = SerialLink(port)
        # Opening the device may reset USB/MCU; settle OFF before ONE handshake.
        time.sleep(.5)
        link.exchange("HELLO")
        link.exchange("ARM")
        started = time.monotonic()
        ready = False
        while time.monotonic() - started < seconds + 1:
            fault = scenario if scenario != "allow" and time.monotonic() - started >= seconds else None
            if not gate.cycle(fault):
                link.exchange("STOP")
                print("차단: LED OFF, 재시작은 새 run 명령으로만 가능합니다.", flush=True)
                return
            link.exchange("RUN", timeout=gate.remaining())
            gate.remaining()  # A late ACK is an error, never a successful fresh run.
            if not ready:
                ready = True
                print("허용: LED ON (테스트 명령이 통과 중)", flush=True)
                if ready_file:
                    Path(ready_file).write_text(json.dumps({"gate_pid": gate.bridge.child.pid}))
            if scenario == "allow" and time.monotonic() - started >= seconds:
                link.exchange("STOP")
                print("시험 종료: LED OFF", flush=True)
                return
            time.sleep(.025)
        raise RuntimeError("scenario did not stop within its bounded window")
    finally:
        if link:
            link.stop()
            link.close()
        if gate:
            gate.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
