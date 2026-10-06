"""LED session: actual Rust approval -> USB RUN, denial/error -> latched STOP."""
import json
from pathlib import Path
import signal
import time

from bench_gate import BenchGate
from bench_link import LinkError, SerialLink


def run(binary, port, scenario, seconds, ready_file=None):
    if not 1 <= seconds <= 30:
        raise ValueError("seconds must be between 1 and 30")
    if scenario not in ("allow", "person", "world-loss", "replay", "invalid-signature"):
        raise ValueError("unknown bench scenario")
    gate = None
    link = None
    previous = {}

    def mark_ready(fault=None):
        if ready_file:
            target = Path(ready_file)
            pending = target.with_suffix(".tmp")
            pending.write_text(json.dumps({"gate_pid": gate.bridge.child.pid, "fault": fault}))
            pending.replace(target)

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
        fault_shown = False
        last_run_at = None
        while time.monotonic() - started < seconds + 1:
            # Polling does not renew output/arming. Acquire the challenge just
            # before the fresh gate round-trip rather than before inter-cycle sleep.
            if link.exchange("STATUS") not in ("ARMED", "ON"):
                print(json.dumps({"event": "device_lease_expired", "status": "LOCKED",
                                  "host_gap_ms": ((time.monotonic() - last_run_at) * 1000
                                                  if last_run_at is not None else None),
                                  "automatic_rearm": False}), flush=True)
                raise LinkError("device lease locked; explicit new run required")
            fault = scenario if scenario != "allow" and time.monotonic() - started >= seconds else None
            if fault and not fault_shown:
                labels = {"person": "가상 사람이 접근합니다", "world-loss": "세계 입력 갱신을 끊습니다",
                          "replay": "이전 서명 입력을 재전송합니다", "invalid-signature": "위조된 서명 입력을 보냅니다"}
                print("시험: " + labels[fault], flush=True)
                mark_ready(fault)
                fault_shown = True
            if not gate.cycle(fault):
                link.exchange("STOP")
                print("차단: LED OFF, 재시작은 새 run 명령으로만 가능합니다.", flush=True)
                return
            run_at = time.monotonic()
            link.exchange("RUN", timeout=gate.remaining())
            last_run_at = run_at
            gate.remaining()  # A late ACK is an error, never a successful fresh run.
            if not ready:
                ready = True
                print("허용: LED ON (테스트 명령이 통과 중)", flush=True)
                mark_ready()
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
