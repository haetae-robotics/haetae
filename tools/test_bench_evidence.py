import contextlib
import io
import itertools
import json
import math
import socket
import tempfile
import traceback
import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bench_evidence
from bench_evidence import (H1_LEASE, H1_STOP_CALL, H2_DEADLINES, H2_ERROR_PREFIX, H2_LEASE, H2_REPLY_PREFIX,
                            TimingRecord, cadence, distribution, h1_timing_loss, h2_timing_loss, scheduling_loss)

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent


def rows(jsonl):
    return [json.loads(line) for line in jsonl.splitlines()]


def trace(stages):
    # Byte-identical to the relay/authorizer print(json.dumps({...})) lines.
    return json.dumps({"event": "permit_ipc_failure", "stages": stages}) + "\n"


# CI artifacts, values and messages unchanged. Run 38038551797 attempt 1 (PR 22, macos-latest),
# invalid-signature: the relay slept through the 200 ms device lease before any fault was due.
PR22_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0,"verification_ms":0}
{"device_ms":773,"on":false,"reason":"hello","sequence":0,"renewed_ms":0,"verification_ms":0}
{"device_ms":776,"on":false,"reason":"bound","sequence":1,"renewed_ms":0,"verification_ms":0}
{"device_ms":778,"on":false,"reason":"armed","sequence":2,"renewed_ms":776,"verification_ms":0}
{"device_ms":962,"on":true,"reason":"run","sequence":3,"renewed_ms":960,"verification_ms":0}
{"device_ms":1020,"on":true,"reason":"run","sequence":4,"renewed_ms":1017,"verification_ms":0}
{"device_ms":1153,"on":true,"reason":"run","sequence":5,"renewed_ms":1151,"verification_ms":0}
{"device_ms":1314,"on":true,"reason":"run","sequence":6,"renewed_ms":1312,"verification_ms":0}
{"device_ms":1488,"on":true,"reason":"run","sequence":7,"renewed_ms":1486,"verification_ms":0}
{"device_ms":1687,"on":false,"reason":"lease","sequence":7,"renewed_ms":1486,"verification_ms":0}
{"device_ms":1689,"on":false,"reason":"stop","sequence":7,"renewed_ms":1486,"verification_ms":0}
""")
PR22_RELAY = ("허용: 보드가 서명을 확인함, LED ON\n" + trace([
    {"op": "RUN", "started_ns": 314178628125, "request_send_started_ns": 314178653500,
     "request_sent_ns": 314178669208, "response_first_byte_ns": 314181058333,
     "response_received_ns": 314181062250, "controller_ack_ns": 314181272083},
    {"op": "RUN", "started_ns": 314312359500, "request_send_started_ns": 314312382833,
     "request_sent_ns": 314312407083, "response_first_byte_ns": 314314311041,
     "response_received_ns": 314314316000, "controller_ack_ns": 314314456958},
    {"op": "RUN", "started_ns": 314473187125, "request_send_started_ns": 314473213208,
     "request_sent_ns": 314473232791, "response_first_byte_ns": 314475007458,
     "response_received_ns": 314475011541, "controller_ack_ns": 314475171291},
    {"op": "RUN", "started_ns": 314647713125, "request_send_started_ns": 314647763541,
     "request_sent_ns": 314647786375, "response_first_byte_ns": 314649251333,
     "response_received_ns": 314649254708, "controller_ack_ns": 314649438375}])
    + "permit 오류: controller locked; explicit operator restart required\n")
PR22_AUTH = trace([
    {"request_first_byte_ns": 314312442083, "request_received_ns": 314312447583, "gate_started_ns": 314312497125,
     "gate_completed_ns": 314314206375, "sign_completed_ns": 314314231000,
     "response_send_started_ns": 314314254041, "response_sent_ns": 314314274083},
    {"request_first_byte_ns": 314473320458, "request_received_ns": 314473324875, "gate_started_ns": 314473366416,
     "gate_completed_ns": 314474925083, "sign_completed_ns": 314474944291,
     "response_send_started_ns": 314474965541, "response_sent_ns": 314474985750},
    {"request_first_byte_ns": 314647822208, "request_received_ns": 314647826583, "gate_started_ns": 314647876916,
     "gate_completed_ns": 314649149416, "sign_completed_ns": 314649164541,
     "response_send_started_ns": 314649180791, "response_sent_ns": 314649190500},
    {}]) + "permit 오류: permit IPC closed before complete frame\n"

# Run 37472584492 attempt 1 (macos-latest), person: a host-wide stall. The authorizer read the
# RUN request 250 ms late, so the relay's 50 ms IPC deadline fired; the frozen emulator logged
# its lease 160 ms late. The authorizer's last stage is a gate denial with no fault (gate
# completed, never signed) that it could no longer send (EPIPE), so no terminal line exists.
PERSON_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0,"verification_ms":0}
{"device_ms":836,"on":false,"reason":"hello","sequence":0,"renewed_ms":0,"verification_ms":0}
{"device_ms":841,"on":false,"reason":"bound","sequence":1,"renewed_ms":0,"verification_ms":1}
{"device_ms":842,"on":false,"reason":"armed","sequence":2,"renewed_ms":841,"verification_ms":0}
{"device_ms":874,"on":true,"reason":"run","sequence":3,"renewed_ms":871,"verification_ms":0}
{"device_ms":976,"on":true,"reason":"run","sequence":4,"renewed_ms":974,"verification_ms":0}
{"device_ms":1077,"on":true,"reason":"run","sequence":5,"renewed_ms":1075,"verification_ms":0}
{"device_ms":1177,"on":true,"reason":"run","sequence":6,"renewed_ms":1171,"verification_ms":0}
{"device_ms":1531,"on":false,"reason":"lease","sequence":6,"renewed_ms":1171,"verification_ms":0}
{"device_ms":1532,"on":false,"reason":"stop","sequence":6,"renewed_ms":1171,"verification_ms":0}
""")
PERSON_RELAY = ("허용: 보드가 서명을 확인함, LED ON\n" + trace([
    {"op": "RUN", "started_ns": 182642094750, "request_send_started_ns": 182642150458,
     "request_sent_ns": 182642191375, "response_first_byte_ns": 182644631208,
     "response_received_ns": 182644636166, "controller_ack_ns": 182644813583},
    {"op": "RUN", "started_ns": 182743657791, "request_send_started_ns": 182743688000,
     "request_sent_ns": 182743716666, "response_first_byte_ns": 182745407291,
     "response_received_ns": 182745412125, "controller_ack_ns": 182745599083},
    {"op": "RUN", "started_ns": 182840141625, "request_send_started_ns": 182840171958,
     "request_sent_ns": 182840202708, "response_first_byte_ns": 182845089291,
     "response_received_ns": 182845099333, "controller_ack_ns": 182845429333},
    {"op": "RUN", "started_ns": 182949495125, "request_send_started_ns": 182949536958,
     "request_sent_ns": 182949573291}]) + "permit 오류: timed out\n")
PERSON_AUTH = trace([
    {"request_first_byte_ns": 182642242666, "request_received_ns": 182642251750, "gate_started_ns": 182642357166,
     "gate_completed_ns": 182644535000, "sign_completed_ns": 182644557666,
     "response_send_started_ns": 182644572250, "response_sent_ns": 182644598666},
    {"request_first_byte_ns": 182743761541, "request_received_ns": 182743766166, "gate_started_ns": 182743826416,
     "gate_completed_ns": 182745308125, "sign_completed_ns": 182745330666,
     "response_send_started_ns": 182745351958, "response_sent_ns": 182745376750},
    {"request_first_byte_ns": 182840239833, "request_received_ns": 182840245291, "gate_started_ns": 182840316583,
     "gate_completed_ns": 182844749458, "sign_completed_ns": 182844823875,
     "response_send_started_ns": 182844967458, "response_sent_ns": 182845042916},
    {"request_first_byte_ns": 183199815416, "request_received_ns": 183199868000, "gate_started_ns": 183200012958,
     "gate_completed_ns": 183319778833, "response_send_started_ns": 183319805666}]) + \
    "permit 오류: [Errno 32] Broken pipe\n"

# Built from PERSON, not a CI log. The relay's last RUN got the authorizer's reply 2 ms after sending
# it, the controller never acknowledged that RUN, and a USB exchange timed out: the RUN itself if the
# reply was a permit, or the relay's STOP if it was a terminal (a stop decision). The device logged
# the relay's STOP 120 ms after the last renewal.
REPLY_STAGES = json.loads(PERSON_RELAY.splitlines()[1])["stages"]
REPLY_STAGES[-1].update(response_first_byte_ns=182951612375, response_received_ns=182951618041)
REPLY_RELAY = (PERSON_RELAY.splitlines(keepends=True)[0] + trace(REPLY_STAGES)
               + H2_ERROR_PREFIX + "H2 USB response timeout\n")
REPLY_EVENTS = PERSON_EVENTS[:8] + [dict(PERSON_EVENTS[-1], device_ms=1291)]
# The authorizer read that request and exited by itself. SENT: it signed a permit and finished
# sending it, then the relay's exit closed the socket. UNSENT, the race: the gate denied, sendall
# wrote the whole terminal, then send_line's deadline check failed, so no terminal line was printed.
REPLY_READ = {"request_first_byte_ns": 182949610458, "request_received_ns": 182949615000,
              "gate_started_ns": 182949668208, "gate_completed_ns": 182951455666}
PERSON_READS = json.loads(PERSON_AUTH.splitlines()[0])["stages"][:3]  # the three RUNs before the last
REPLY_AUTH_SENT = (trace(PERSON_READS[1:] + [dict(REPLY_READ, sign_completed_ns=182951479125,
                                                  response_send_started_ns=182951501041,
                                                  response_sent_ns=182951529583), {}])
                   + H2_ERROR_PREFIX + "permit IPC closed before complete frame\n")
REPLY_AUTH_UNSENT = (trace(PERSON_READS + [dict(REPLY_READ, response_send_started_ns=182951489333)])
                     + H2_ERROR_PREFIX + "permit IPC frame deadline expired\n")

# H1, run 37462489051 attempt 1 (macos-latest), allow: one gate round trip took 53.1 ms.
H1_ALLOW_LOG = """\
허용: LED ON (테스트 명령이 통과 중)
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/Users/runner/work/haetae/haetae/tools/bench_host.py", line 59, in run
    if not gate.cycle(fault):
           ^^^^^^^^^^^^^^^^^
  File "/Users/runner/work/haetae/haetae/tools/bench_gate.py", line 119, in cycle
    require_fresh_actuation(step, elapsed, now_ms(), 200, 50)
  File "/Users/runner/work/haetae/haetae/ros/haetae_gate/bridge.py", line 63, in require_fresh_actuation
    raise StaleActuation(
bridge.StaleActuation: stale actuation response: elapsed 53.1 ms >= 50 ms
"""
H1_ALLOW_READY = {"gate_pid": 3076, "fault": None}
H1_ALLOW_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0}
{"device_ms":647,"on":false,"reason":"hello","sequence":0,"renewed_ms":0}
{"device_ms":647,"on":false,"reason":"armed","sequence":1,"renewed_ms":647}
{"device_ms":647,"on":false,"reason":"armed","sequence":2,"renewed_ms":647}
{"device_ms":649,"on":true,"reason":"run","sequence":3,"renewed_ms":649}
{"device_ms":750,"on":true,"reason":"run","sequence":4,"renewed_ms":649}
{"device_ms":751,"on":true,"reason":"run","sequence":5,"renewed_ms":751}
{"device_ms":786,"on":true,"reason":"run","sequence":6,"renewed_ms":751}
{"device_ms":787,"on":true,"reason":"run","sequence":7,"renewed_ms":787}
{"device_ms":867,"on":true,"reason":"run","sequence":8,"renewed_ms":787}
{"device_ms":868,"on":true,"reason":"run","sequence":9,"renewed_ms":868}
{"device_ms":900,"on":true,"reason":"run","sequence":10,"renewed_ms":868}
{"device_ms":901,"on":true,"reason":"run","sequence":11,"renewed_ms":901}
{"device_ms":999,"on":true,"reason":"run","sequence":12,"renewed_ms":901}
{"device_ms":1003,"on":true,"reason":"run","sequence":13,"renewed_ms":1003}
{"device_ms":1029,"on":true,"reason":"run","sequence":14,"renewed_ms":1003}
{"device_ms":1031,"on":true,"reason":"run","sequence":15,"renewed_ms":1030}
{"device_ms":1137,"on":true,"reason":"run","sequence":16,"renewed_ms":1030}
{"device_ms":1191,"on":false,"reason":"stop","sequence":17,"renewed_ms":1030}
""")

# H1, run 37178004418 (macos-latest, earlier bench code, same messages), person: STATUS still
# answered ON, then the lease ended before the RUN, which the guard refused as unarmed.
H1_UNARMED_LOG = """\
허용: LED ON (테스트 명령이 통과 중)
Traceback (most recent call last):
  File "/Users/runner/work/haetae/haetae/tools/bench_link.py", line 73, in exchange
    raise ValueError("unexpected USB response: " + line.decode("ascii"))
ValueError: unexpected USB response: H1 ERR b0e05db76e163280 6 7 LOCKED unarmed

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/Users/runner/work/haetae/haetae/tools/bench_host.py", line 58, in run
    link.exchange("RUN", timeout=gate.remaining())
  File "/Users/runner/work/haetae/haetae/tools/bench_link.py", line 78, in exchange
    raise LinkError(str(exc)) from exc
bench_link.LinkError: unexpected USB response: H1 ERR b0e05db76e163280 6 7 LOCKED unarmed
"""
H1_UNARMED_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0}
{"device_ms":742,"on":false,"reason":"hello","sequence":0,"renewed_ms":0}
{"device_ms":742,"on":false,"reason":"armed","sequence":1,"renewed_ms":742}
{"device_ms":742,"on":false,"reason":"armed","sequence":2,"renewed_ms":742}
{"device_ms":749,"on":true,"reason":"run","sequence":3,"renewed_ms":748}
{"device_ms":858,"on":true,"reason":"run","sequence":4,"renewed_ms":748}
{"device_ms":865,"on":true,"reason":"run","sequence":5,"renewed_ms":864}
{"device_ms":1036,"on":true,"reason":"run","sequence":6,"renewed_ms":864}
{"device_ms":1064,"on":false,"reason":"lease","sequence":6,"renewed_ms":864}
{"device_ms":1066,"on":false,"reason":"unarmed","sequence":6,"renewed_ms":864}
{"device_ms":1066,"on":false,"reason":"stop","sequence":7,"renewed_ms":864}
""")

# H1, run 37178378909 (ubuntu-latest, earlier bench code, same messages), invalid-signature: the
# first gate round trip timed out before positive control, so the host never wrote its ready file.
H1_PRE_ON_LOG = """\
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/home/runner/work/haetae/haetae/tools/bench_host.py", line 54, in run
    if not gate.cycle(fault):
           ^^^^^^^^^^^^^^^^^
  File "/home/runner/work/haetae/haetae/tools/bench_gate.py", line 117, in cycle
    step = self.motion()
           ^^^^^^^^^^^^^
  File "/home/runner/work/haetae/haetae/tools/bench_gate.py", line 100, in motion
    return self.signed(envelope)
           ^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/haetae/haetae/tools/bench_gate.py", line 87, in signed
    return self.bridge.request({"t": now_ms(), "k": "signed", "data": json.dumps(envelope)})
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/haetae/haetae/ros/haetae_gate/bridge.py", line 98, in request
    raise BridgeFailure("gate response timeout")
bridge.BridgeFailure: gate response timeout
"""
H1_PRE_ON_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0}
{"device_ms":563,"on":false,"reason":"hello","sequence":0,"renewed_ms":0}
{"device_ms":563,"on":false,"reason":"armed","sequence":1,"renewed_ms":563}
{"device_ms":564,"on":false,"reason":"armed","sequence":2,"renewed_ms":563}
{"device_ms":614,"on":false,"reason":"stop","sequence":3,"renewed_ms":563}
""")

# H1, run 37178635608 (macos-latest, earlier bench code, same messages): after ON, before the fault
# was due (ready fault null), a gate round trip took 54.3 ms (attempt 1, replay) and 60.2 ms
# (attempt 2, world-loss). Both attempts were reported as "stopped before injecting intended fault".
H1_REPLAY_LOG = """\
허용: LED ON (테스트 명령이 통과 중)
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/Users/runner/work/haetae/haetae/tools/bench_host.py", line 54, in run
    if not gate.cycle(fault):
           ^^^^^^^^^^^^^^^^^
  File "/Users/runner/work/haetae/haetae/tools/bench_gate.py", line 119, in cycle
    require_fresh_actuation(step, elapsed, now_ms(), 200, 50)
  File "/Users/runner/work/haetae/haetae/ros/haetae_gate/bridge.py", line 63, in require_fresh_actuation
    raise StaleActuation(
bridge.StaleActuation: stale actuation response: elapsed 54.3 ms >= 50 ms
"""
H1_REPLAY_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0}
{"device_ms":713,"on":false,"reason":"hello","sequence":0,"renewed_ms":0}
{"device_ms":713,"on":false,"reason":"armed","sequence":1,"renewed_ms":713}
{"device_ms":713,"on":false,"reason":"armed","sequence":2,"renewed_ms":713}
{"device_ms":735,"on":true,"reason":"run","sequence":3,"renewed_ms":734}
{"device_ms":876,"on":true,"reason":"run","sequence":4,"renewed_ms":734}
{"device_ms":930,"on":false,"reason":"stop","sequence":5,"renewed_ms":734}
""")
H1_WORLD_LOSS_LOG = H1_REPLAY_LOG.replace("elapsed 54.3 ms", "elapsed 60.2 ms")  # otherwise identical
H1_WORLD_LOSS_EVENTS = rows("""\
{"device_ms":0,"on":false,"reason":"boot","sequence":0,"renewed_ms":0}
{"device_ms":700,"on":false,"reason":"hello","sequence":0,"renewed_ms":0}
{"device_ms":700,"on":false,"reason":"armed","sequence":1,"renewed_ms":700}
{"device_ms":700,"on":false,"reason":"armed","sequence":2,"renewed_ms":700}
{"device_ms":741,"on":true,"reason":"run","sequence":3,"renewed_ms":740}
{"device_ms":786,"on":true,"reason":"run","sequence":4,"renewed_ms":740}
{"device_ms":847,"on":false,"reason":"stop","sequence":5,"renewed_ms":740}
""")

# An uncaught crash as the interpreter prints it. haetae-permit turns every RuntimeError, OSError
# and ValueError (all timing errors) into one "permit 오류:" line, so a timing loss never has one.
CRASH = """\
Traceback (most recent call last):
  File "/Users/runner/work/haetae/haetae/tools/permit_authorizer.py", line 72, in approve
    allowed = self.gate.cycle(fault)
KeyError: 'cmd'
"""

INSTALL, NONCE = "0123456789abcdef" * 2, "fedcba9876543210" * 2


def h2_reply(reason, status="ERR"):
    return f"{H2_REPLY_PREFIX}H2 {status} {INSTALL} 1 1 7 9 1688 {NONCE} LOCKED {reason}"


def relay_error(error):
    return PR22_RELAY.replace(H2_LEASE, error)


def off(device_ms, reason, renewed_ms=1486):
    return {"device_ms": device_ms, "on": False, "reason": reason, "sequence": 7, "renewed_ms": renewed_ms}


ON = PR22_EVENTS[:9]  # through the last renewal (run at 1486)
ARMED = PR22_EVENTS[:4]  # armed with the ARM lease counted from 776


def real_host_log(scenario="person", statuses=None, failing_cycle=None, deny_cycle=None, failing_op=None,
                  link_error=None):
    """The real bench_host.run against fakes; returns its log as the host process writes it.

    statuses: STATUS replies in order (None: always ON). failing_cycle: that gate round trip takes
    53.1 ms. deny_cycle: that gate cycle denies with no fault injected. failing_op: every exchange
    of that op raises LinkError(link_error). The clock advances 0.1 s per reading, so the allow
    scenario completes after its 1 s window, while a person fault is not due within three cycles.
    """
    import bench_host
    from bench_link import LinkError
    status = iter(statuses) if statuses is not None else itertools.repeat("ON")
    clock, cycles = [0.], [0]
    class Gate:
        def __init__(self, binary):
            self.bridge = SimpleNamespace(child=SimpleNamespace(pid=1))
        def cycle(self, fault):
            cycles[0] += 1
            if cycles[0] == failing_cycle:
                from bridge import require_fresh_actuation
                require_fresh_actuation({"cmd": {"linear": .25, "angular": 0},
                                         "status": {"active_expires_ms": 10 ** 15, "world_age_ms": 0}},
                                        53.1, 0, 200, 50)
            return cycles[0] != deny_cycle
        def remaining(self):
            return .05
        def close(self):
            pass
    class Link:
        def __init__(self, port):
            pass
        def exchange(self, op, timeout=.05):
            if op == failing_op:
                raise LinkError(link_error)
            if op == "STATUS":
                return next(status)
            return {"HELLO": "LOCKED", "ARM": "ARMED", "RUN": "ON", "STOP": "LOCKED"}[op]
        def stop(self):
            pass  # SerialLink.stop swallows LinkError and OSError
        def close(self):
            pass
    def monotonic():
        clock[0] += .1
        return clock[0]
    with patch.object(bench_host, "BenchGate", Gate), patch.object(bench_host, "SerialLink", Link), \
            patch.object(bench_host.time, "sleep", lambda value: None), \
            patch.object(bench_host.time, "monotonic", monotonic), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        try:
            bench_host.run(Path("fake-gate"), "fake-port", scenario, 1)
        except Exception as exc:  # uncaught in the host process: the interpreter prints this
            return output.getvalue() + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    raise AssertionError("host did not fail")


class BenchEvidenceTest(unittest.TestCase):
    def test_interrupt_stop_evidence_cannot_fall_back_to_lease_expiry(self):
        tree = ast.parse(Path(__file__).with_name('bench_verify.py').read_text())
        attempt = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'host_attempt')
        assignment = next(node for node in ast.walk(attempt) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'stopped' for target in node.targets)
                          and isinstance(node.value, ast.Call) and getattr(node.value.func, 'id', None) == 'wait_for')
        probe = assignment.value.args[0]
        lease = {'reason': 'lease'}
        device = SimpleNamespace(stopped=lambda after, reason: lease if reason == 'lease' else None)
        for interrupt, scenario, expected, accepted in (
                ('gate-kill', 'allow', 'stop', False),
                (None, 'person', 'stop', False),
                (None, 'allow', 'stop', True),
                ('kill', 'allow', 'lease', True),
                ('pause', 'allow', 'lease', True)):
            scope = dict(device=device, positive={'device_ms': 10}, interrupt=interrupt,
                         scenario=scenario, expected=expected)
            predicate = eval(compile(ast.Expression(probe), 'actual-bench-stop-predicate', 'eval'), scope)
            self.assertEqual(bool(predicate()), accepted)

    def test_only_proven_availability_loss_is_accepted_as_fail_closed(self):
        marker = {"event": "device_lease_expired", "status": "LOCKED",
                  "host_gap_ms": 250, "automatic_rearm": False}
        events = [{"device_ms": 10, "on": True, "renewed_ms": 10},
                  {"device_ms": 249, "on": False, "reason": "lease"}]
        def log(value):
            return json.dumps(value) + "\ndevice lease locked; explicit new run required"
        self.assertEqual(scheduling_loss(log(marker), 1, "allow", events), events[-1])
        for change in ({"host_gap_ms": 199}, {"host_gap_ms": float("nan")},
                       {"status": "ON"}, {"automatic_rearm": True}):
            changed = dict(marker, **change)
            self.assertIsNone(scheduling_loss(log(changed), 1, "allow", events))
        for code, scenario in ((0, "allow"), (1, "person"), (-9, "allow")):
            self.assertIsNone(scheduling_loss(log(marker), code, scenario, events))
        for changed in (events[1:], events + [{"on": True}],
                        [events[0], dict(events[1], reason="stop")],
                        [events[0], dict(events[1], device_ms=311)]):
            self.assertIsNone(scheduling_loss(log(marker), 1, "allow", changed))
        self.assertIsNone(scheduling_loss("unknown failure", 1, "allow", events))


class H2TimingLossTest(unittest.TestCase):
    def test_both_real_failures_are_inconclusive_with_timing_evidence(self):
        lease = h2_timing_loss(PR22_RELAY, 1, PR22_AUTH, PR22_EVENTS)
        self.assertEqual({key: lease[key] for key in ("kind", "error", "positive_control", "stop_reason",
                                                       "renewal_reason", "last_run_to_off_ms",
                                                       "emulator_lateness_ms", "max_renewal_gap_ms")},
                         {"kind": "lease", "error": H2_LEASE, "positive_control": True, "stop_reason": "lease",
                          "renewal_reason": "run", "last_run_to_off_ms": 201, "emulator_lateness_ms": 1,
                          "max_renewal_gap_ms": 184})
        self.assertEqual(lease["relay_last_stage"]["controller_ack_ns"], 314649438375)
        self.assertAlmostEqual(lease["authorizer_read_delay_ms"], .036, places=3)
        self.assertAlmostEqual(lease["gate_cycle_ms"], 1.27, places=2)
        deadline = h2_timing_loss(PERSON_RELAY, 1, PERSON_AUTH, PERSON_EVENTS)
        self.assertEqual((deadline["kind"], deadline["error"], deadline["stop_reason"],
                          deadline["last_run_to_off_ms"], deadline["emulator_lateness_ms"]),
                         ("deadline", "timed out", "lease", 360, 160))
        self.assertAlmostEqual(deadline["authorizer_read_delay_ms"], 250.242, places=3)
        self.assertAlmostEqual(deadline["gate_cycle_ms"], 119.766, places=3)
        self.assertEqual(json.loads(json.dumps(deadline, allow_nan=False)), deadline)
        # Documented boundary: a terminal the authorizer could not send (so it logged no terminal line)
        # and that never reached the relay does not prevent a restart.
        undelivered = json.loads(PERSON_AUTH.splitlines()[0])["stages"][-1]
        self.assertEqual(("gate_completed_ns" in undelivered, "sign_completed_ns" in undelivered), (True, False))

    def test_relay_must_exit_1_with_a_timing_message(self):
        for code in (0, 2, -9, None):
            self.assertIsNone(h2_timing_loss(PR22_RELAY, code, PR22_AUTH, PR22_EVENTS))
        for error in ("permit IPC closed before complete frame", "incorrect controller permit acknowledgment",
                      "incorrect authenticated controller state", "invalid authorizer permit",
                      "controller context changed", "STOP did not lock controller", "H2 USB closed",
                      "oversized H2 response", "[Errno 32] Broken pipe", "permit IPC trailing frame bytes",
                      H2_REPLY_PREFIX + "H2 OK " + INSTALL, H2_LEASE + " ", "timed out!"):
            self.assertIsNone(h2_timing_loss(relay_error(error), 1, PR22_AUTH, PR22_EVENTS), error)
        self.assertIsNone(h2_timing_loss(PR22_RELAY + "trailing line\n", 1, PR22_AUTH, PR22_EVENTS))

    def test_a_fault_or_stop_decision_makes_any_loss_a_failure(self):
        # PERSON_RELAY never received a reply; a terminal the authorizer logged fails the attempt even so.
        for line in ("Authorizer fault injected: invalid-signature\n", "Authorizer terminal: gate_denied\n",
                     "Authorizer terminal: gate_expired\n", "Authorizer terminal: authorizer_failure\n"):
            self.assertIsNone(h2_timing_loss(PR22_RELAY, 1, line + PR22_AUTH, PR22_EVENTS))
            self.assertIsNone(h2_timing_loss(PERSON_RELAY, 1, PERSON_AUTH + line, PERSON_EVENTS))

    def test_a_reply_the_relay_received_counts_only_after_a_completed_send(self):
        # REPLY_RELAY's last RUN got the authorizer's reply, which the controller never acknowledged.
        # The authorizer prints a terminal only after send_line returns, and send_line checks its
        # deadline again after sendall, so a terminal can reach the relay with no terminal line. Only
        # a completed send (response_sent_ns) by the stage that read that request shows that any
        # terminal it carried was logged.
        for error in ("H2 USB response timeout", "H2 USB write timeout", "authorizer permit arrived late"):
            relay = REPLY_RELAY.replace("H2 USB response timeout", error)
            loss = h2_timing_loss(relay, 1, REPLY_AUTH_SENT, REPLY_EVENTS)  # control: a permit it sent
            self.assertEqual((loss["kind"], loss["error"], loss["stop_reason"], loss["last_run_to_off_ms"]),
                             ("deadline", error, "stop", 120))
            self.assertAlmostEqual(loss["gate_cycle_ms"], 1.787, places=3)
            for auth in (REPLY_AUTH_UNSENT,  # the race: no completed send and no terminal line
                         trace(PERSON_READS) + H2_ERROR_PREFIX + "[Errno 32] Broken pipe\n",  # no stage read it
                         ""):  # no trace
                self.assertIsNone(h2_timing_loss(relay, 1, auth, REPLY_EVENTS), (error, auth))
        # A terminal the relay never received and the authorizer never logged does not prevent a restart.
        self.assertEqual(h2_timing_loss(PERSON_RELAY, 1, REPLY_AUTH_UNSENT, PERSON_EVENTS)["kind"], "deadline")

    def test_a_crash_traceback_in_either_log_is_never_a_timing_loss(self):
        # An uncaught crash after the relay's 50 ms deadline fired must not pass as a deadline loss.
        self.assertEqual(h2_timing_loss(PERSON_RELAY, 1, PERSON_AUTH, PERSON_EVENTS)["kind"], "deadline")  # control
        auth_last = PERSON_AUTH.splitlines(keepends=True)[-1]
        for auth in (PERSON_AUTH + CRASH, PERSON_AUTH.replace(auth_last, CRASH)):
            self.assertIsNone(h2_timing_loss(PERSON_RELAY, 1, auth, PERSON_EVENTS))
        self.assertIsNone(h2_timing_loss(PR22_RELAY, 1, PR22_AUTH + CRASH, PR22_EVENTS))
        relay_last = PERSON_RELAY.splitlines(keepends=True)[-1]
        self.assertIsNone(h2_timing_loss(PERSON_RELAY.replace(relay_last, CRASH + relay_last), 1, PERSON_AUTH,
                                         PERSON_EVENTS))

    def test_loss_must_come_from_one_relay_renewal_loop_trace(self):
        marker = PR22_RELAY.splitlines(keepends=True)[1]
        for replacement in ("", marker * 2, trace([]), trace({"op": "RUN"}), trace([{}]),
                            trace([{"op": "STATUS", "started_ns": 1}]), trace(["RUN"])):
            self.assertIsNone(h2_timing_loss(PR22_RELAY.replace(marker, replacement), 1, PR22_AUTH,
                                             PR22_EVENTS), replacement)

    def test_device_must_show_one_fail_closed_stop(self):
        for events in (ON,                                            # never OFF
                       PR22_EVENTS + [dict(PR22_EVENTS[8], device_ms=1700)],  # ON again after OFF
                       ON + [off(1685, "lease")],                     # lease 199 ms: not timing
                       ON + [off(1689, "stop")],                      # LOCKED without a lease expiry
                       ON + [off(1687, "unarmed")],                   # only the reply variant accepts it
                       ON + [off(1600, "stale")],
                       ON + [{"on": False, "reason": "lease", "renewed_ms": 1486}],  # malformed row
                       ON + [off("1687", "lease")]):
            self.assertIsNone(h2_timing_loss(PR22_RELAY, 1, PR22_AUTH, events))
        for reason in ("signature", "replay", "protocol", "partial", "tx", "hello", "exhausted"):
            for relay, auth in ((PR22_RELAY, PR22_AUTH), (PERSON_RELAY, PERSON_AUTH)):
                self.assertIsNone(h2_timing_loss(relay, 1, auth, ON + [off(1690, reason), off(1691, "stop")]), reason)
        for reason in ("stop", "stale"):
            loss = h2_timing_loss(PERSON_RELAY, 1, PERSON_AUTH, ON + [off(1500, reason)])
            self.assertEqual((loss["kind"], loss["stop_reason"], loss["emulator_lateness_ms"]),
                             ("deadline", reason, None))
        self.assertIsNone(h2_timing_loss(PERSON_RELAY, 1, PERSON_AUTH, ON + [off(1685, "lease")]))

    def test_a_stop_or_stale_refusal_counts_only_within_the_observation_bound(self):
        # With no lease row before it, a late STOP or stale refusal may have ended an ON that outlived
        # its 200 ms lease: never a timing loss. Up to the 300 ms bound that every passing attempt
        # meets it still counts, because the emulator writes one row per loop pass and a lease that
        # ends in the same pass as a STOP shows only as the STOP.
        timeout = relay_error("H2 USB response timeout")
        for relay, auth in ((PERSON_RELAY, PERSON_AUTH), (timeout, PR22_AUTH)):
            for reason in ("stop", "stale"):
                for device_ms, delay in ((1736, 250), (1786, 300)):  # last renewal at 1486
                    loss = h2_timing_loss(relay, 1, auth, ON + [off(device_ms, reason)])
                    self.assertEqual((loss["kind"], loss["stop_reason"], loss["last_run_to_off_ms"]),
                                     ("deadline", reason, delay))
                for late in (1787, 1886, 2386, 1480):  # 301, 400 and 900 ms, and a malformed -6 ms
                    self.assertIsNone(h2_timing_loss(relay, 1, auth, ON + [off(late, reason)]), (reason, late))
                    self.assertIsNone(h2_timing_loss(relay, 1, auth, ON + [off(late, reason), off(late + 1, "stop")]))
        # Control: the lease row itself counts however late the emulator wrote it (360 ms in PERSON).
        late_lease = h2_timing_loss(PERSON_RELAY, 1, PERSON_AUTH, ON + [off(1886, "lease"), off(1887, "stop")])
        self.assertEqual((late_lease["stop_reason"], late_lease["emulator_lateness_ms"]), ("lease", 200))
        # Before ON the same bound runs from the ARM renewal (776).
        before_on = h2_timing_loss(timeout, 1, PR22_AUTH, ARMED + [off(1076, "stop", 776)])
        self.assertEqual((before_on["positive_control"], before_on["last_run_to_off_ms"]), (False, 300))
        for late in (1077, 1376):  # 301 and 600 ms after ARM
            self.assertIsNone(h2_timing_loss(timeout, 1, PR22_AUTH, ARMED + [off(late, "stop", 776)]), late)

    def test_before_on_the_first_failure_after_armed_decides(self):
        arm_lease = h2_timing_loss(PR22_RELAY, 1, PR22_AUTH, ARMED + [off(977, "lease", 776), off(978, "stop", 776)])
        self.assertEqual((arm_lease["kind"], arm_lease["positive_control"], arm_lease["renewal_reason"],
                          arm_lease["last_run_to_off_ms"]), ("lease", False, "armed", 201))
        self.assertIsNone(h2_timing_loss(PR22_RELAY, 1, PR22_AUTH, ARMED + [off(975, "lease", 776)]))
        timeout = relay_error("H2 USB response timeout")
        self.assertEqual(h2_timing_loss(timeout, 1, PR22_AUTH, ARMED + [off(800, "stop", 776)])["stop_reason"], "stop")
        # A content failure is never skipped to reach a later timing-like row.
        for reason in ("signature", "replay", "protocol", "partial"):
            self.assertIsNone(h2_timing_loss(timeout, 1, PR22_AUTH,
                                             ARMED + [off(800, reason, 776), off(801, "stop", 776)]), reason)
        self.assertIsNone(h2_timing_loss(timeout, 1, PR22_AUTH, ARMED[:3] + [off(800, "stop", 0)]))  # never armed

    def test_locked_reply_counts_only_after_a_full_lease(self):
        for reason in ("unarmed", "lease"):
            relay = relay_error(h2_reply(reason))
            loss = h2_timing_loss(relay, 1, PR22_AUTH, ON + [off(1687, "lease"), off(1689, "unarmed"),
                                                             off(1690, "stop")])
            self.assertEqual((loss["kind"], loss["stop_reason"], loss["last_run_to_off_ms"]), ("lease", "lease", 201))
            merged = h2_timing_loss(relay, 1, PR22_AUTH, ON + [off(1686, "unarmed"), off(1688, "stop")])
            self.assertEqual((merged["kind"], merged["stop_reason"]), ("lease", "unarmed"))
            self.assertIsNone(h2_timing_loss(relay, 1, PR22_AUTH, ON + [off(1685, "unarmed"), off(1688, "stop")]))
            for first in ("signature", "replay", "stale", "stop"):
                self.assertIsNone(h2_timing_loss(relay, 1, PR22_AUTH,
                                                 ON + [off(1700, first), off(1701, "unarmed")]), first)
        for reply in (h2_reply("stale"), h2_reply("signature"), h2_reply("replay"), h2_reply("protocol"),
                      h2_reply("partial"), h2_reply("unarmed", "OK"), h2_reply("unarmed").upper(),
                      h2_reply("unarmed").replace(" 1688 ", " 01688 ")):
            self.assertIsNone(h2_timing_loss(relay_error(reply), 1, PR22_AUTH,
                                             ON + [off(1687, "lease"), off(1689, "stop")]), reply)


class H1TimingLossTest(unittest.TestCase):
    def test_real_failures_are_inconclusive(self):
        stale = h1_timing_loss(H1_ALLOW_LOG, 1, H1_ALLOW_READY, H1_ALLOW_EVENTS)
        self.assertEqual((stale["kind"], stale["stop_reason"], stale["last_run_to_off_ms"], stale["positive_control"],
                          stale["max_renewal_gap_ms"]), ("deadline", "stop", 161, True, 102))
        self.assertEqual(stale["error"], "bridge.StaleActuation: stale actuation response: elapsed 53.1 ms >= 50 ms")
        unarmed = h1_timing_loss(H1_UNARMED_LOG, 1, {"gate_pid": 1, "fault": None}, H1_UNARMED_EVENTS)
        self.assertEqual((unarmed["kind"], unarmed["stop_reason"], unarmed["last_run_to_off_ms"]), ("lease", "lease", 200))
        before_on = h1_timing_loss(H1_PRE_ON_LOG, 1, None, H1_PRE_ON_EVENTS)
        self.assertEqual((before_on["kind"], before_on["positive_control"], before_on["stop_reason"],
                          before_on["last_run_to_off_ms"]), ("deadline", False, "stop", 51))
        for log, events, delay in ((H1_REPLAY_LOG, H1_REPLAY_EVENTS, 196),
                                   (H1_WORLD_LOSS_LOG, H1_WORLD_LOSS_EVENTS, 107)):
            before_fault = h1_timing_loss(log, 1, {"gate_pid": 1, "fault": None}, events)
            self.assertEqual((before_fault["kind"], before_fault["positive_control"], before_fault["stop_reason"],
                              before_fault["last_run_to_off_ms"]), ("deadline", True, "stop", delay))

    def test_timing_loss_after_the_fault_or_with_other_errors_stays_a_failure(self):
        for code in (0, 2, -9, None):
            self.assertIsNone(h1_timing_loss(H1_ALLOW_LOG, code, H1_ALLOW_READY, H1_ALLOW_EVENTS))
        for ready in ({"gate_pid": 1, "fault": "person"}, "unreadable", [], {"gate_pid": 1, "fault": False}):
            self.assertIsNone(h1_timing_loss(H1_ALLOW_LOG, 1, ready, H1_ALLOW_EVENTS), ready)
        for line in ("시험: 가상 사람이 접근합니다\n", "차단: LED OFF, 재시작은 새 run 명령으로만 가능합니다.\n",
                     "시험 종료: LED OFF\n"):
            self.assertIsNone(h1_timing_loss(line + H1_ALLOW_LOG, 1, H1_ALLOW_READY, H1_ALLOW_EVENTS))
        last = H1_ALLOW_LOG.splitlines()[-1]
        for error in ("bridge.ExpiredActuation: stale actuation response: world expired",
                      "bridge.StaleActuation: stale actuation response: missing expiry or world age",
                      "bridge.StaleActuation: stale actuation response: elapsed 53.1 ms >= 60 ms",
                      "bridge.BridgeFailure: gate output closed", "bridge.BridgeFailure: malformed gate response",
                      "bench_link.LinkError: USB closed", "bench_link.LinkError: incorrect device state",
                      "bench_link.LinkError: unexpected USB response: H1 ERR b0e05db76e163280 6 7 LOCKED stale",
                      "bench_link.LinkError: unexpected USB response: H1 ERR b0e05db76e163280 6 7 LOCKED replay",
                      "RuntimeError: scenario did not stop within its bounded window",
                      "ValueError: USB response timeout", "AssertionError: gate response timeout"):
            self.assertIsNone(h1_timing_loss(H1_ALLOW_LOG.replace(last, error), 1, H1_ALLOW_READY,
                                             H1_ALLOW_EVENTS), error)
        self.assertIsNone(h1_timing_loss(last + "\n", 1, H1_ALLOW_READY, H1_ALLOW_EVENTS))  # no traceback

    def test_device_rules_match_h2(self):
        lease_log = H1_ALLOW_LOG.replace(H1_ALLOW_LOG.splitlines()[-1], "bench_link.LinkError: " + H1_LEASE)
        on = H1_ALLOW_EVENTS[:18]
        def h1_off(device_ms, reason):
            return {"device_ms": device_ms, "on": False, "reason": reason, "sequence": 16, "renewed_ms": 1030}
        loss = h1_timing_loss(lease_log, 1, H1_ALLOW_READY, on + [h1_off(1230, "lease"), h1_off(1231, "stop")])
        self.assertEqual((loss["kind"], loss["stop_reason"], loss["last_run_to_off_ms"]), ("lease", "lease", 200))
        for events in (on + [h1_off(1229, "lease")], on + [h1_off(1231, "stop")],
                       on + [h1_off(1230, "lease"), dict(on[-1], device_ms=1240)],
                       H1_ALLOW_EVENTS[:4] + [h1_off(700, "replay"), h1_off(701, "stop")]):
            self.assertIsNone(h1_timing_loss(lease_log, 1, H1_ALLOW_READY, events))
        self.assertIsNone(h1_timing_loss(H1_ALLOW_LOG, 1, H1_ALLOW_READY,
                                         H1_ALLOW_EVENTS[:4] + [h1_off(700, "signature"), h1_off(701, "stop")]))

    def test_locked_reply_counts_only_for_a_lease_refusal(self):
        # The H2 rule: a LOCKED unarmed or lease reply is a lease loss. A stale or content refusal
        # stays a failure, even with the device's full lease logged first (run 37178004418's rows).
        ready = {"gate_pid": 1, "fault": None}
        for reason in ("unarmed", "lease"):
            loss = h1_timing_loss(H1_UNARMED_LOG.replace(" LOCKED unarmed", " LOCKED " + reason), 1, ready,
                                  H1_UNARMED_EVENTS)
            self.assertEqual((loss["kind"], loss["stop_reason"], loss["last_run_to_off_ms"]), ("lease", "lease", 200))
        for reason in ("stale", "replay", "protocol", "partial", "hello", "stop"):
            log = H1_UNARMED_LOG.replace(" LOCKED unarmed", " LOCKED " + reason)
            self.assertEqual(log.splitlines()[-1], "bench_link.LinkError: unexpected USB response: "
                                                   "H1 ERR b0e05db76e163280 6 7 LOCKED " + reason)
            self.assertIsNone(h1_timing_loss(log, 1, ready, H1_UNARMED_EVENTS), reason)

    def test_a_stop_or_stale_refusal_counts_only_within_the_observation_bound(self):
        # The H2 rule: with no lease row first, a STOP or stale refusal later than 300 ms fails at once.
        def h1_off(device_ms, reason, renewed_ms):
            return {"device_ms": device_ms, "on": False, "reason": reason, "sequence": 16, "renewed_ms": renewed_ms}
        on = H1_ALLOW_EVENTS[:18]  # last renewal at 1030
        for reason in ("stop", "stale"):
            loss = h1_timing_loss(H1_ALLOW_LOG, 1, H1_ALLOW_READY, on + [h1_off(1330, reason, 1030)])
            self.assertEqual((loss["kind"], loss["stop_reason"], loss["last_run_to_off_ms"]), ("deadline", reason, 300))
            for late in (1331, 1480):  # 301 and 450 ms after the last renewal
                self.assertIsNone(h1_timing_loss(H1_ALLOW_LOG, 1, H1_ALLOW_READY, on + [h1_off(late, reason, 1030)]),
                                  (reason, late))
        armed = H1_PRE_ON_EVENTS[:4]  # before positive control, from the ARM renewal at 563
        before_on = h1_timing_loss(H1_PRE_ON_LOG, 1, None, armed + [h1_off(863, "stop", 563)])
        self.assertEqual((before_on["positive_control"], before_on["last_run_to_off_ms"]), (False, 300))
        for late in (864, 1163):  # 301 and 600 ms after ARM
            self.assertIsNone(h1_timing_loss(H1_PRE_ON_LOG, 1, None, armed + [h1_off(late, "stop", 563)]), late)


class DriftGuardTest(unittest.TestCase):
    """The classifiers match exact strings; pin them to the code that produces them."""

    def test_h2_messages_and_guard_reasons_exist_in_source(self):
        relay_source = (TOOLS / "permit_link.py").read_text() + (TOOLS / "permit_ipc.py").read_text()
        for text in (H2_DEADLINES - {"timed out"}) | {H2_LEASE}:
            self.assertIn('"' + text + '"', relay_source)
        self.assertIn('"' + H2_REPLY_PREFIX + '"', relay_source)
        launcher = (ROOT / "haetae-permit").read_text()
        self.assertIn('"' + H2_ERROR_PREFIX + '" + str(exc)', launcher)
        # Timing errors print one line, never a traceback (h2_timing_loss rejects any traceback).
        self.assertIn("except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:", launcher)
        authorizer = (TOOLS / "permit_authorizer.py").read_text()
        for marker in bench_evidence.H2_DECISIONS:
            self.assertIn('"' + marker + ' "', authorizer)
        guard = (ROOT / "hardware/uno_r4_permit/permit_guard.h").read_text()
        for text in ("lease_ms = 200", 'fail("lease")', 'fail("unarmed")', 'fail("stale")', 'fail("stop")',
                     'reason_ = run ? "run" : "armed"', '"H2 %s %s %lu %lu %lu %lu %lu %s %s %s\\n"',
                     'on_ ? "ON" : armed_ ? "ARMED" : bound_ ? "BOUND" : "LOCKED"'):
            self.assertIn(text, guard)
        left, right = socket.socketpair()  # "timed out" is the socket timeout text, not a literal
        with left, right:
            left.settimeout(.001)
            with self.assertRaises(OSError) as caught:
                left.recv(1)
        self.assertIn(str(caught.exception), H2_DEADLINES)

    def test_stop_bound_is_the_passing_observation_bound(self):
        # A STOP or stale refusal counts as a timing loss only within the stop delay that every
        # passing host attempt must meet; neither side may move without the other.
        self.assertEqual(bench_evidence.OBSERVATION_MS, 300)
        self.assertIn("assert 0 <= delay <= 300,", (TOOLS / "bench_verify.py").read_text())
        self.assertIn("require(0 <= delay <= 300,", (TOOLS / "permit_verify.py").read_text())

    def run_relay(self, statuses, late_op=None, terminal_run=None, stop_error=None):
        """The real permit_link.relay against fakes; returns its log as haetae-permit writes it.

        terminal_run: the authorizer answers that RUN (counting from 1) with a gate_denied terminal, a
        stop decision, instead of a permit. stop_error: the relay's STOP raises LinkError(stop_error).
        """
        import permit_link
        clock, requests, status = [0.], [], iter(statuses)
        class Link:
            current = {"sequence": 0}
            def __init__(self, *args):
                pass
            def query(self, op):
                if op == "STOP" and stop_error is not None:
                    raise permit_link.LinkError(stop_error)
                return next(status) if op == "STATUS" else "LOCKED"
            def execute(self, request, permit, timeout):
                pass
            def stop(self):
                pass
            def close(self):
                pass
        class Peer:
            def connect(self, path):
                pass
            def settimeout(self, timeout):
                pass
            def sendall(self, raw):
                requests.append(json.loads(raw)["op"])
            def recv(self, size):
                if requests[-1] == late_op and requests.count(late_op) > 1:
                    clock[0] += .06
                if requests[-1] == "RUN" and requests.count("RUN") == terminal_run:
                    return b'{"kind":"terminal","reason":"gate_denied"}\n'
                return b'{"kind":"permit","signature":"00","duration":200,"remaining_ms":50}\n'
            def close(self):
                pass
        with patch.object(permit_link, "PermitLink", Link), \
                patch.object(permit_link.socket, "socket", lambda *args: Peer()), \
                patch.object(permit_link.time, "sleep", lambda value: None), \
                patch.object(permit_link.time, "monotonic", lambda: clock[0]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                permit_link.relay("fake-port", "public-install", "fake-socket")
            except (RuntimeError, OSError, ValueError) as exc:  # what haetae-permit catches and prints
                return output.getvalue() + H2_ERROR_PREFIX + str(exc) + "\n"
        self.fail("relay did not fail")

    def test_real_relay_output_is_classified(self):
        lease = h2_timing_loss(self.run_relay(["ON", "ON", "LOCKED"]), 1, "", PR22_EVENTS)
        self.assertEqual((lease["kind"], lease["relay_last_stage"]["op"]), ("lease", "RUN"))
        deadline = h2_timing_loss(self.run_relay(["ON", "ON"], late_op="RUN"), 1, "", PERSON_EVENTS)
        self.assertEqual((deadline["kind"], deadline["error"]), ("deadline", "permit IPC frame deadline expired"))

    def run_authorizer(self, reply, stall=False):
        """The real permit_authorizer.serve against fakes; returns its log as haetae-permit writes it.

        It reads one RUN request and answers with reply; then the relay closes the socket. stall: the
        authorizer is descheduled for 0.6 s right after sendall wrote the whole reply, past the 0.5 s
        send deadline that send_line checks again after sendall.
        """
        import permit_authorizer
        clock = [100.]
        request = {"install": "01" * 16, "epoch": 1, "generation": 1, "sequence": 3, "challenge": 9,
                   "issued": 0, "nonce": "02" * 16, "op": "RUN"}
        frames = iter([json.dumps(request).encode() + b"\n", b""])
        class Authorizer:
            def __init__(self, *args):
                pass
            def approve(self, value, stages=None):
                return reply
            def close(self):
                pass
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def settimeout(self, timeout):
                pass
            def recv(self, size):
                return next(frames)
            def sendall(self, raw):
                if stall:
                    clock[0] += .6
        class Server:
            def bind(self, path):
                Path(path).touch()  # serve's chmod and unlink then act on a real file
            def listen(self, backlog):
                pass
            def settimeout(self, timeout):
                pass
            def accept(self):
                return Connection(), None
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(permit_authorizer, "Authorizer", Authorizer), \
                patch.object(permit_authorizer.socket, "socket", lambda *args: Server()), \
                patch.object(permit_authorizer.time, "monotonic", lambda: clock[0]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                permit_authorizer.serve("fake-gate", "fake-key", "fake-deployment", Path(tmp) / "auth.sock",
                                        "person", 1)
            except (RuntimeError, OSError, ValueError) as exc:  # what haetae-permit catches and prints
                return output.getvalue() + H2_ERROR_PREFIX + str(exc) + "\n"
            return output.getvalue()

    def test_real_authorizer_output_is_classified(self):
        # The authorizer prints a terminal only after send_line returns, and send_line stamps
        # response_sent_ns only after the deadline check that follows sendall. A reply written in
        # full whose check then failed may be a terminal the relay acted on although the log has no
        # terminal line, so a relay that received such a reply is never a timing loss.
        terminal = {"kind": "terminal", "reason": "gate_denied"}
        permit = {"kind": "permit", "signature": "00" * 32, "duration": 200, "remaining_ms": 50}
        self.assertEqual(self.run_authorizer(terminal), "Authorizer terminal: gate_denied\n")
        for reply, stall in ((permit, False), (permit, True), (terminal, True)):
            log = self.run_authorizer(reply, stall)
            self.assertNotIn("Authorizer terminal:", log)
            read = json.loads(log.splitlines()[0])["stages"][0]
            self.assertEqual("response_sent_ns" in read, not stall)
            # The relay's last RUN got this reply, the controller never acknowledged it, and a USB
            # exchange timed out: the RUN for a permit, the relay's STOP for a terminal.
            first, sending = read["request_first_byte_ns"], read["response_send_started_ns"]
            relay = "허용: 보드가 서명을 확인함, LED ON\n" + trace([
                {"op": "RUN", "started_ns": first - 30000, "request_send_started_ns": first - 20000,
                 "request_sent_ns": first - 10000, "response_first_byte_ns": sending + 10000,
                 "response_received_ns": sending + 20000}]) + H2_ERROR_PREFIX + "H2 USB response timeout\n"
            self.assertEqual(h2_timing_loss(relay, 1, log, REPLY_EVENTS) is None, stall, (reply, stall))

    def test_real_relay_stop_decision_without_a_terminal_line_is_never_a_timing_loss(self):
        # The race through the real relay and authorizer: the relay's last RUN received a gate_denied
        # terminal and its STOP timed out, while the authorizer had written that whole terminal, then
        # was descheduled past its send deadline, so its log has no terminal line. The completed-send
        # rule reads the key the relay's receive_line stamps for a received reply. If that key drifted,
        # the rule would never apply and this stop decision would be restarted.
        relay = self.run_relay(["ON", "ON"], terminal_run=2, stop_error="H2 USB response timeout")
        lines = relay.splitlines()
        self.assertEqual(lines[-1], H2_ERROR_PREFIX + "H2 USB response timeout")
        stages = json.loads(lines[-2])["stages"]
        self.assertEqual([stage["op"] for stage in stages], ["BIND", "ARM", "RUN", "RUN"])
        self.assertIn("response_received_ns", stages[-1])
        self.assertNotIn("controller_ack_ns", stages[-1])
        # Run after the relay, so the authorizer's stage is the first read at or after the relay's last send.
        stalled = self.run_authorizer({"kind": "terminal", "reason": "gate_denied"}, stall=True)
        self.assertNotIn("Authorizer terminal:", stalled)
        read = bench_evidence.authorizer_stage(stalled, stages[-1])
        self.assertEqual(("response_send_started_ns" in read, "response_sent_ns" in read), (True, False))
        self.assertIsNone(h2_timing_loss(relay, 1, stalled, REPLY_EVENTS))
        # Control: the same relay log is a timing loss when that authorizer stage completed its send.
        sent = self.run_authorizer({"kind": "permit", "signature": "00" * 32, "duration": 200, "remaining_ms": 50})
        loss = h2_timing_loss(relay, 1, sent, REPLY_EVENTS)
        self.assertEqual((loss["kind"], loss["error"], loss["stop_reason"], loss["last_run_to_off_ms"]),
                         ("deadline", "H2 USB response timeout", "stop", 120))

    def test_h1_messages_and_guard_reasons_exist_in_source(self):
        host = (TOOLS / "bench_host.py").read_text()
        self.assertIn('"' + H1_LEASE + '"', host)
        for prefix in bench_evidence.H1_DECISIONS:
            self.assertIn('print("' + prefix, host)
        link = (TOOLS / "bench_link.py").read_text()
        for text in bench_evidence.H1_USB_DEADLINES:
            self.assertIn('LinkError("' + text + '")', link)
        self.assertIn('"' + bench_evidence.H1_REPLY_PREFIX + '"', link)
        self.assertIn('BridgeFailure("gate response timeout")', (ROOT / "ros/haetae_gate/bridge.py").read_text())
        guard = (ROOT / "hardware/uno_r4_bench/guard.h").read_text()
        for text in ("lease_ms = 200", 'fail("lease")', 'fail("unarmed")', 'fail("stale")', 'fail("stop")',
                     'reason_ = "armed"', 'reason_ = "run"', '"H1 %s %s %lu %lu %s %s\\n"'):
            self.assertIn(text, guard)

    def test_real_host_output_is_classified(self):
        lease_events = H1_ALLOW_EVENTS[:18] + [
            {"device_ms": 1230, "on": False, "reason": "lease", "sequence": 16, "renewed_ms": 1030},
            {"device_ms": 1231, "on": False, "reason": "stop", "sequence": 17, "renewed_ms": 1030}]
        lease = h1_timing_loss(real_host_log(statuses=["ARMED", "ON", "LOCKED"]), 1, H1_ALLOW_READY, lease_events)
        self.assertEqual((lease["kind"], lease["error"]), ("lease", "bench_link.LinkError: " + H1_LEASE))
        stale = h1_timing_loss(real_host_log(statuses=["ARMED", "ON"], failing_cycle=2), 1, H1_ALLOW_READY,
                               H1_ALLOW_EVENTS)
        self.assertEqual((stale["kind"], stale["error"]),
                         ("deadline", "bridge.StaleActuation: stale actuation response: elapsed 53.1 ms >= 50 ms"))

    def test_real_host_stop_after_a_stop_decision_is_never_a_timing_loss(self):
        # bench_host prints its gate-stop or allow-completion line only after STOP returns, so a STOP
        # that times out leaves no decision line. Its frame still shows the decision was made.
        lease_events = H1_ALLOW_EVENTS[:18] + [  # the STOP never arrived
            {"device_ms": 1230, "on": False, "reason": "lease", "sequence": 16, "renewed_ms": 1030},
            {"device_ms": 1231, "on": False, "reason": "stop", "sequence": 17, "renewed_ms": 1030}]
        for error in bench_evidence.H1_USB_DEADLINES:
            # Positive control: the same error from the same host before any decision is a timing loss.
            control = real_host_log("allow", failing_op="RUN", link_error=error)
            self.assertEqual(h1_timing_loss(control, 1, None, H1_ALLOW_EVENTS)["kind"], "deadline", error)
            completion = real_host_log("allow", failing_op="STOP", link_error=error)
            denial = real_host_log("person", deny_cycle=2, failing_op="STOP", link_error=error)  # no fault yet
            denial_before_on = real_host_log("person", deny_cycle=1, failing_op="STOP", link_error=error)
            for log in (completion, denial, denial_before_on):
                lines = log.splitlines()
                self.assertEqual(lines[-1], "bench_link.LinkError: " + error)
                self.assertIn(H1_STOP_CALL, [line.strip() for line in lines])
                self.assertFalse(any(line.startswith(bench_evidence.H1_DECISIONS) for line in lines))
            for events in (H1_ALLOW_EVENTS, lease_events):
                self.assertIsNone(h1_timing_loss(completion, 1, H1_ALLOW_READY, events), error)
                self.assertIsNone(h1_timing_loss(denial, 1, H1_ALLOW_READY, events), error)
            self.assertIsNone(h1_timing_loss(denial_before_on, 1, None, H1_PRE_ON_EVENTS), error)


class CadenceTest(unittest.TestCase):
    def test_renewal_cadence_and_the_50_ms_path_from_device_logs(self):
        self.assertEqual(cadence(PR22_EVENTS, "H2"), {"renewal_gap_ms": [57, 134, 161, 174],
                                                      "arm_to_first_run_ms": [184],
                                                      "status_to_run_verified_ms": [2, 3, 2, 2, 2]})
        self.assertEqual(cadence(H1_ALLOW_EVENTS, "H1"), {"renewal_gap_ms": [102, 36, 81, 33, 102, 27],
                                                          "arm_to_first_run_ms": [2],
                                                          "status_to_run_verified_ms": [2, 1, 1, 1, 1, 4, 1]})
        self.assertEqual(cadence(H1_PRE_ON_EVENTS, "H1"), {"renewal_gap_ms": [], "arm_to_first_run_ms": [],
                                                           "status_to_run_verified_ms": []})
        self.assertEqual(distribution([57, 134, 161, 174]), {"n": 4, "p50": 134, "p99": 174, "max": 174})
        self.assertEqual(distribution([float("nan"), True, None]), {"n": 0})
        self.assertEqual(distribution(range(1, 201))["p99"], 198)

    def test_measurement_never_changes_an_outcome(self):
        record = TimingRecord("H2")
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "person.jsonl"
            log.write_text("".join(json.dumps(event) + "\n" for event in PERSON_EVENTS) + '{"partial')
            record.measure("person", 1, "person", "inconclusive", log)
            record.measure("person", 2, "person-retry1", "passed", Path(tmp) / "missing.jsonl")
        first, second = record.attempts
        self.assertEqual((first["renewal_gap_ms"]["max"], first["arm_to_first_run_ms"]), (103, 30))
        self.assertIn("measurement_error", second)
        record.inconclusive.append({"case": "person", "attempt": 1})
        payload = record.payload()
        self.assertEqual((payload["blocking"], payload["inconclusive_total"], payload["inconclusive_by_case"],
                          payload["pooled"]["renewal_gap_ms"]["n"]), (False, 1, {"person": 1}, 3))
        json.dumps(payload, allow_nan=False)
        self.assertTrue(all(math.isfinite(value) for value in payload["pooled"]["renewal_gap_ms"].values()))


if __name__ == "__main__":
    unittest.main()
