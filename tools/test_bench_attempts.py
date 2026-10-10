"""Retry policy and harness wiring for timing-inconclusive attempts.

stdout is always captured: the ::warning:: lines must appear only in the bench
step, never as annotations from the unit-test step. Fakes stand in for every
device and process; nothing is started or signalled (fake PIDs are out of range).
"""
import ast
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_evidence
import bench_verify
import permit_verify
from bench_evidence import HOST_ATTEMPTS, Inconclusive, TimingRecord, warning
from test_bench_evidence import (CRASH, H1_ALLOW_EVENTS, H1_ALLOW_LOG, H1_ALLOW_READY, H1_PRE_ON_EVENTS,
                                 H1_PRE_ON_LOG, H1_REPLAY_EVENTS, H1_REPLAY_LOG, H1_UNARMED_EVENTS, H1_UNARMED_LOG,
                                 H1_WORLD_LOSS_EVENTS, H1_WORLD_LOSS_LOG, PERSON_AUTH, PERSON_EVENTS, PERSON_RELAY,
                                 PR22_AUTH, PR22_EVENTS, PR22_RELAY, REPLY_AUTH_SENT, REPLY_AUTH_UNSENT, REPLY_EVENTS,
                                 REPLY_RELAY, real_host_log)

UNUSED_PID = 2 ** 30  # above every Linux/macOS pid_max
LOSS = {"kind": "lease", "error": "controller locked; explicit operator restart required",
        "positive_control": True, "stop_reason": "lease", "last_run_to_off_ms": 201}
# Rows of a passing allow attempt (PR 22 macos-latest artifact): operator stop 89 ms after renewal.
ALLOW_EVENTS = [{"device_ms": 693, "on": False, "reason": "armed", "sequence": 2, "renewed_ms": 691},
                {"device_ms": 740, "on": True, "reason": "run", "sequence": 3, "renewed_ms": 739},
                {"device_ms": 1617, "on": True, "reason": "run", "sequence": 20, "renewed_ms": 1616},
                {"device_ms": 1705, "on": False, "reason": "stop", "sequence": 20, "renewed_ms": 1616}]
ARM_LEASE_EVENTS = PR22_EVENTS[:4] + [
    {"device_ms": 977, "on": False, "reason": "lease", "sequence": 2, "renewed_ms": 776},
    {"device_ms": 978, "on": False, "reason": "stop", "sequence": 2, "renewed_ms": 776}]


def fake_device(out, events, attempts):
    class Device:
        def __init__(self, *args):
            name = args[-1]
            attempts.append(name)
            self.port = "fake-pty"
            (out / (name + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in events))
        def events(self):
            return [dict(event) for event in events]
        def on(self):
            return next((event for event in reversed(events) if event["on"]), None)
        def stopped(self, after, reason=None):
            return next((event for event in events if not event["on"] and event["device_ms"] >= after
                         and (reason is None or event["reason"] == reason)), None)
        def close(self):
            pass
    return Device


class RetryPolicyTest(unittest.TestCase):
    def run_policy(self, module, outcomes, **flags):
        calls, timing = [], TimingRecord("H2" if module is permit_verify else "H1")
        def attempt(*args):
            calls.append(args)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return dict(outcome)
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "host_attempt", attempt), \
                patch.object(module, "OUT", Path(tmp)), contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                if module is permit_verify:
                    result = permit_verify.host_case(Path(tmp), {}, "person", "person", timing=timing, **flags)
                else:
                    result = bench_verify.host_case("person", "person", timing=timing, **flags)
            except Exception as exc:
                result = exc
        labels = [args[2] if module is permit_verify else args[0] for args in calls]
        return result, labels, calls, timing.payload(), output.getvalue()

    def test_an_inconclusive_attempt_restarts_fresh_and_only_a_later_full_pass_counts(self):
        for module in (permit_verify, bench_verify):
            result, labels, _, payload, output = self.run_policy(module, [Inconclusive(LOSS), {"passed": True}])
            self.assertEqual((result["case"], result["attempt"], result["log"]), ("person", 2, "person-retry1"))
            self.assertEqual(labels, ["person", "person-retry1"])
            self.assertEqual([row["outcome"] for row in payload["attempts"]], ["inconclusive", "passed"])
            self.assertEqual((payload["inconclusive_total"], payload["inconclusive_by_case"]), (1, {"person": 1}))
            self.assertEqual(payload["inconclusive"][0]["log"], "person")
            lines = output.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("::warning::"))
            self.assertIn("person attempt 1/" + str(HOST_ATTEMPTS) + " timing-inconclusive, not a pass", lines[0])

    def test_all_inconclusive_fails_and_keeps_every_attempt_evidence(self):
        for module in (permit_verify, bench_verify):
            result, labels, _, payload, output = self.run_policy(module, [Inconclusive(LOSS)] * HOST_ATTEMPTS)
            self.assertIsInstance(result, AssertionError)
            self.assertIn("inconclusive never counts as a pass", str(result))
            self.assertEqual(labels, ["person", "person-retry1", "person-retry2"])
            self.assertEqual(payload["inconclusive_total"], HOST_ATTEMPTS)
            self.assertEqual([row["outcome"] for row in payload["attempts"]], ["inconclusive"] * HOST_ATTEMPTS)
            self.assertEqual(output.count("::warning::"), HOST_ATTEMPTS)
            lines = output.splitlines()
            for line in lines[:2]:
                self.assertIn("; restarting with a fresh device emulator", line)
            # The last line must not announce a restart that never comes.
            self.assertIn("person attempt 3/" + str(HOST_ATTEMPTS) + " timing-inconclusive, not a pass", lines[2])
            self.assertTrue(lines[2].endswith("; no attempts left; the case fails"
                                              " (inconclusive never counts as a pass)"), lines[2])
            self.assertNotIn("restarting", lines[2])

    def test_any_other_failure_is_never_retried(self):
        for module in (permit_verify, bench_verify):
            for outcomes, error, count in (
                    ([AssertionError("scenario failed"), {"passed": True}], "scenario failed", 1),
                    ([RuntimeError("relay crashed"), {"passed": True}], "relay crashed", 1),
                    ([Inconclusive(LOSS), AssertionError("ON after stop"), {"passed": True}], "ON after stop", 2)):
                result, labels, _, payload, output = self.run_policy(module, outcomes)
                self.assertIsInstance(result, type(outcomes[count - 1]))
                self.assertEqual((str(result), len(labels)), (error, count))
                self.assertEqual(payload["attempts"][-1]["outcome"], "failed")
                self.assertEqual(output.count("::warning::"), count - 1)

    def test_interrupted_cases_pass_their_interruption_to_every_attempt(self):
        _, _, calls, _, _ = self.run_policy(permit_verify, [Inconclusive(LOSS), {"passed": True}], pause=True)
        self.assertEqual([args[4:] for args in calls], [(True, False)] * 2)
        _, _, calls, _, _ = self.run_policy(permit_verify, [Inconclusive(LOSS), {"passed": True}],
                                            kill_authorizer=True)
        self.assertEqual([args[4:] for args in calls], [(False, True)] * 2)
        _, _, calls, _, _ = self.run_policy(bench_verify, [Inconclusive(LOSS), {"passed": True}], interrupt="pause")
        self.assertEqual([args[2] for args in calls], ["pause"] * 2)

    def test_a_case_first_removes_only_its_own_stale_retry_logs(self):
        # Retry logs left by an earlier local run must not sit beside this run's attempts. Every other
        # log stays, including those of cases whose names share a prefix (replayed-permit, usb-replay).
        for module, suffixes in ((bench_verify, (".jsonl", "-host.log", "-ready.json")),
                                 (permit_verify, (".jsonl", "-authorizer.log", "-relay.log"))):
            with tempfile.TemporaryDirectory() as tmp:
                out, present = Path(tmp), []
                stale = {"replay-retry" + str(number) + suffix for number in (1, 2) for suffix in suffixes}
                kept = {name + suffix for name in ("replay", "replay-retry", "replayed-permit", "usb-replay",
                                                   "person-retry1") for suffix in suffixes} | {"report.json"}
                for name in stale | kept:
                    (out / name).write_text("earlier run\n")
                def attempt(*args):
                    present.append({path.name for path in out.iterdir()})
                    return {"passed": True}
                with patch.object(module, "host_attempt", attempt), patch.object(module, "OUT", out), \
                        contextlib.redirect_stdout(io.StringIO()):
                    if module is permit_verify:
                        permit_verify.host_case(out, {}, "replay", "replay", timing=TimingRecord("H2"))
                    else:
                        bench_verify.host_case("replay", "replay", timing=TimingRecord("H1"))
            self.assertEqual(present, [kept], module.__name__)
        # The pattern matches no other case's logs only while no case name contains "-retry".
        for name in ("bench_verify.py", "permit_verify.py"):
            tree = ast.parse(Path(__file__).with_name(name).read_text())
            main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
            self.assertEqual([node.value for node in ast.walk(main) if isinstance(node, ast.Constant)
                              and isinstance(node.value, str) and "-retry" in node.value], [], name)

    def test_inconclusive_is_only_caught_by_the_attempt_loop(self):
        for base in (AssertionError, RuntimeError, OSError, ValueError):
            self.assertFalse(issubclass(Inconclusive, base))
        self.assertIs(permit_verify.Inconclusive, bench_evidence.Inconclusive)
        self.assertIs(bench_verify.Inconclusive, bench_evidence.Inconclusive)
        line = warning("H2", "person", 1, {"kind": "deadline", "error": "50%\r\nlate"}, "fresh start")
        self.assertEqual(line.splitlines(), [line])
        self.assertIn("50%25%0D%0Alate", line)


class PermitHarnessTest(unittest.TestCase):
    """permit_verify.host_attempt with fake device and processes."""

    def run_attempt(self, events, relay_text, code, auth_text="", late_auth="", reaches_on=True,
                    scenario="person", via_case=False, auth_exit=0, **flags):
        calls, attempts = [], []
        class Process:
            def __init__(self, argv, stdout, stderr, **kwargs):
                self.role, self.log, self.returncode, self.pid = argv[2], stdout, None, UNUSED_PID
                stdout.write(auth_text if self.role == "authorizer" else relay_text)
                stdout.flush()
                if self.role == "authorizer" or reaches_on:
                    Path(argv[argv.index("--ready-file") + 1]).write_text("{}")
                elif self.role == "relay":
                    self.returncode = code  # exited before ON
            def poll(self):
                return self.returncode
            def wait(self, timeout=None):
                calls.append(self.role + ".wait")
                if self.role == "authorizer" and self.returncode is None:
                    if auth_exit is None:  # still alive after the grace; stop_pair kills it
                        raise subprocess.TimeoutExpired("authorizer", timeout)
                    self.log.write(late_auth)  # printed after the relay had already exited
                    self.log.flush()
                    self.returncode = auth_exit
                elif self.role == "relay":
                    self.returncode = code
                return self.returncode
            def kill(self):
                calls.append(self.role + ".kill")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch.object(permit_verify, "OUT", out), \
                    patch.object(permit_verify, "Device", fake_device(out, events, attempts)), \
                    patch.object(permit_verify.subprocess, "Popen", Process), \
                    patch.object(permit_verify, "stop_pair", lambda authorizer, relay: calls.append("stop_pair")), \
                    patch.object(permit_verify.os, "kill", lambda pid, sig: calls.append("signal")), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    if via_case:
                        result = permit_verify.host_case(out, {}, "case", scenario, timing=TimingRecord("H2"),
                                                         **flags)
                    else:
                        result = permit_verify.host_attempt(out, {}, "case", scenario, **flags)
                except Exception as exc:
                    result = exc
        return result, calls, attempts

    def test_positive_loss_is_classified_from_final_logs(self):
        result, calls, _ = self.run_attempt(PR22_EVENTS, PR22_RELAY, 1, late_auth=PR22_AUTH,
                                            scenario="invalid-signature")
        self.assertIsInstance(result, Inconclusive)
        self.assertEqual(result.evidence["kind"], "lease")
        self.assertIsNotNone(result.evidence["gate_cycle_ms"])  # the authorizer's late trace was read
        self.assertLess(calls.index("authorizer.wait"), calls.index("stop_pair"))

    def test_terminal_printed_after_the_relay_exit_is_read(self):
        result, calls, _ = self.run_attempt(ALLOW_EVENTS, "허용: 보드가 서명을 확인함, LED ON\n차단: 서명 허가 중단, LED OFF\n",
                                            0, late_auth="Authorizer terminal: operator_complete\n", scenario="allow")
        self.assertEqual((result["passed"], result["stop_reason"], result["last_run_to_off_ms"]), (True, "stop", 89))

    def test_loss_after_the_fault_stays_a_failure(self):
        result, _, _ = self.run_attempt(PERSON_EVENTS, PERSON_RELAY, 1,
                                        auth_text="Authorizer fault injected: person\n" + PERSON_AUTH)
        self.assertIsInstance(result, AssertionError)
        self.assertIn("scenario failed", str(result))

    def test_pause_and_kill_classify_only_a_loss_before_on(self):
        for flags in ({"pause": True}, {"kill_authorizer": True}):
            result, calls, _ = self.run_attempt(ARM_LEASE_EVENTS, PR22_RELAY, 1, reaches_on=False, **flags)
            self.assertIsInstance(result, Inconclusive, flags)
            self.assertFalse(result.evidence["positive_control"])
            self.assertNotIn("signal", calls)
            self.assertNotIn("authorizer.kill", calls)
        # After the pause, the same deadline loss that a positive scenario retries is judged by
        # the pause rules alone: 360 ms exceeds the 300 ms observation bound, one attempt only.
        result, calls, attempts = self.run_attempt(PERSON_EVENTS, PERSON_RELAY, 1, auth_text=PERSON_AUTH,
                                                   via_case=True, scenario="allow", pause=True)
        self.assertIsInstance(result, AssertionError)
        self.assertNotIsInstance(result, Inconclusive)
        self.assertEqual((attempts, calls.count("signal")), (["case"], 2))
        result, _, attempts = self.run_attempt(PERSON_EVENTS, PERSON_RELAY, 1, auth_text=PERSON_AUTH,
                                               via_case=True, scenario="person")
        self.assertIsInstance(result, AssertionError)  # the same loss without a pause: three attempts
        self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"])

    def test_an_authorizer_crash_after_the_relay_deadline_runs_once(self):
        result, _, attempts = self.run_attempt(PERSON_EVENTS, PERSON_RELAY, 1, auth_text=PERSON_AUTH + CRASH,
                                               via_case=True)
        self.assertIsInstance(result, AssertionError)
        self.assertIn("scenario failed", str(result))
        self.assertEqual(attempts, ["case"])
        result, _, attempts = self.run_attempt(PERSON_EVENTS, PERSON_RELAY, 1, auth_text=PERSON_AUTH,
                                               via_case=True)  # control: the same loss without the crash
        self.assertIn("inconclusive never counts as a pass", str(result))
        self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"])

    def test_a_cut_short_authorizer_log_is_never_classified(self):
        # REPLY_RELAY's last RUN got the authorizer's reply but no controller ACK, then a USB exchange
        # timed out. The authorizer prints a terminal only after sending it, so if it is still alive
        # 1 s after the relay exit (stop_pair kills it) or was ended by a signal, its log may lack a
        # stop decision the relay received, even with a trace that shows a completed send.
        for auth_exit in (None, -9):
            result, calls, attempts = self.run_attempt(REPLY_EVENTS, REPLY_RELAY, 1, auth_text=REPLY_AUTH_SENT,
                                                       auth_exit=auth_exit, via_case=True)
            self.assertIsInstance(result, AssertionError, auth_exit)
            self.assertIn("scenario failed", str(result))
            self.assertEqual(attempts, ["case"])
            self.assertLess(calls.index("authorizer.wait"), calls.index("stop_pair"))
        # Control: the same logs from an authorizer that exited by itself are a timing loss, ...
        result, _, _ = self.run_attempt(REPLY_EVENTS, REPLY_RELAY, 1, late_auth=REPLY_AUTH_SENT, auth_exit=1)
        self.assertIsInstance(result, Inconclusive)
        self.assertEqual((result.evidence["kind"], result.evidence["stop_reason"],
                          result.evidence["last_run_to_off_ms"]), ("deadline", "stop", 120))
        # ... and a complete log shows the terminal it sent before exiting: an ordinary failure.
        result, _, attempts = self.run_attempt(REPLY_EVENTS, REPLY_RELAY, 1,
                                               late_auth="Authorizer terminal: gate_denied\n", via_case=True)
        self.assertIsInstance(result, AssertionError)
        self.assertIn("scenario failed", str(result))
        self.assertEqual(attempts, ["case"])

    def test_a_terminal_that_reached_the_relay_unlogged_runs_once(self):
        # The race: sendall wrote the authorizer's whole terminal, then send_line's deadline check
        # failed, so the authorizer exited by itself with a trace but no terminal line, while the
        # relay acted on that terminal and its STOP timed out. No completed send: an ordinary failure.
        result, _, attempts = self.run_attempt(REPLY_EVENTS, REPLY_RELAY, 1, auth_text=REPLY_AUTH_UNSENT,
                                               auth_exit=1, via_case=True)
        self.assertIsInstance(result, AssertionError)
        self.assertIn("scenario failed", str(result))
        self.assertEqual(attempts, ["case"])
        # Control: the same relay log after a completed send is restarted, and never passes.
        result, _, attempts = self.run_attempt(REPLY_EVENTS, REPLY_RELAY, 1, late_auth=REPLY_AUTH_SENT,
                                               auth_exit=1, via_case=True)
        self.assertIn("inconclusive never counts as a pass", str(result))
        self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"])

    def test_killed_authorizer_is_never_reaped_before_its_session_is_stopped(self):
        result, calls, _ = self.run_attempt(PR22_EVENTS, "permit 오류: [Errno 32] Broken pipe\n", 1,
                                            scenario="allow", kill_authorizer=True)
        self.assertEqual((result["passed"], result["stop_reason"]), (True, "lease"))
        self.assertIn("authorizer.kill", calls)
        self.assertNotIn("authorizer.wait", calls)


class BenchHarnessTest(unittest.TestCase):
    """bench_verify.host_attempt with fake device and host process."""

    def run_attempt(self, events, log_text, ready_value, code, reaches_on=True, scenario="allow",
                    interrupt=None, via_case=False):
        calls, attempts = [], []
        class Process:
            def __init__(self, argv, stdout, stderr, **kwargs):
                self.returncode, self.pid = None, UNUSED_PID
                stdout.write(log_text)
                stdout.flush()
                if reaches_on:
                    Path(argv[-1]).write_text(json.dumps(ready_value))
                else:
                    self.returncode = code  # exited before positive control
            def poll(self):
                return self.returncode
            def wait(self, timeout=None):
                self.returncode = code
                return code
            def kill(self):
                calls.append("host.kill")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch.object(bench_verify, "OUT", out), \
                    patch.object(bench_verify, "Device", fake_device(out, events, attempts)), \
                    patch.object(bench_verify.subprocess, "Popen", Process), \
                    patch.object(bench_verify.os, "kill", lambda pid, sig: calls.append("signal")), \
                    patch.object(bench_verify.os, "killpg", lambda pid, sig: calls.append("killpg")), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    if via_case:
                        result = bench_verify.host_case("case", scenario, interrupt, timing=TimingRecord("H1"))
                    else:
                        result = bench_verify.host_attempt("case", scenario, interrupt)
                except Exception as exc:
                    result = exc
        return result, calls, attempts

    def test_positive_loss_before_the_fault_is_inconclusive(self):
        result, _, _ = self.run_attempt(H1_ALLOW_EVENTS, H1_ALLOW_LOG, H1_ALLOW_READY, 1)
        self.assertIsInstance(result, Inconclusive)
        self.assertEqual((result.evidence["kind"], result.evidence["stop_reason"]), ("deadline", "stop"))
        result, _, _ = self.run_attempt(H1_ALLOW_EVENTS, H1_ALLOW_LOG, {"gate_pid": 1, "fault": "person"}, 1,
                                        scenario="person")
        self.assertIsInstance(result, AssertionError)
        self.assertNotIsInstance(result, Inconclusive)

    def test_every_fault_scenario_restarts_a_loss_after_on_and_before_its_fault(self):
        # host_attempt must classify before it requires the injected fault: until the fault is
        # due, ready.json keeps fault null. Run 37178635608 failed replay and world-loss this way.
        before_fault = {"gate_pid": 1, "fault": None}
        losses = [(H1_ALLOW_EVENTS, H1_ALLOW_LOG, "deadline"), (H1_UNARMED_EVENTS, H1_UNARMED_LOG, "lease")]
        real = {"world-loss": (H1_WORLD_LOSS_EVENTS, H1_WORLD_LOSS_LOG, "deadline"),
                "replay": (H1_REPLAY_EVENTS, H1_REPLAY_LOG, "deadline")}
        for scenario in ("person", "world-loss", "replay", "invalid-signature"):
            for events, log, kind in losses + ([real[scenario]] if scenario in real else []):
                result, _, _ = self.run_attempt(events, log, before_fault, 1, scenario=scenario)
                self.assertIsInstance(result, Inconclusive, (scenario, kind))
                self.assertEqual(result.evidence["kind"], kind, scenario)
            result, _, attempts = self.run_attempt(H1_ALLOW_EVENTS, H1_ALLOW_LOG, before_fault, 1,
                                                   scenario=scenario, via_case=True)
            self.assertIn("inconclusive never counts as a pass", str(result), scenario)
            self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"], scenario)
            # Control: the same loss after the fault was injected fails at once.
            result, _, attempts = self.run_attempt(H1_ALLOW_EVENTS, H1_ALLOW_LOG, {"gate_pid": 1, "fault": scenario},
                                                   1, scenario=scenario, via_case=True)
            self.assertNotIsInstance(result, Inconclusive, scenario)
            self.assertIn("host scenario failed", str(result), scenario)
            self.assertEqual(attempts, ["case"], scenario)

    def test_a_stop_that_times_out_after_a_decision_runs_once(self):
        ready = {"gate_pid": 1, "fault": None}
        for error in ("USB response timeout", "USB write timeout"):
            # Allow completion: the operator STOP must reach the device; exit 1 is a failure.
            completion = real_host_log("allow", failing_op="STOP", link_error=error)
            result, _, attempts = self.run_attempt(ALLOW_EVENTS, completion, ready, 1, via_case=True)
            self.assertNotIsInstance(result, Inconclusive)
            self.assertIn("host scenario failed", str(result))
            self.assertEqual(attempts, ["case"])
            # A gate denial before the person fault was injected stays the existing failure.
            denial = real_host_log("person", deny_cycle=2, failing_op="STOP", link_error=error)
            result, _, attempts = self.run_attempt(ALLOW_EVENTS, denial, ready, 1, scenario="person", via_case=True)
            self.assertNotIsInstance(result, Inconclusive)
            self.assertIn("stopped before injecting intended fault", str(result))
            self.assertEqual(attempts, ["case"])
            # Control: the same error before any decision is restarted, and never passes.
            control = real_host_log("allow", failing_op="RUN", link_error=error)
            result, _, attempts = self.run_attempt(ALLOW_EVENTS, control, ready, 1, via_case=True)
            self.assertIn("inconclusive never counts as a pass", str(result))
            self.assertEqual(attempts, ["case", "case-retry1", "case-retry2"])

    def test_interrupted_cases_classify_only_a_loss_before_positive_control(self):
        for interrupt in ("kill", "pause", "gate-kill"):
            result, calls, _ = self.run_attempt(H1_PRE_ON_EVENTS, H1_PRE_ON_LOG, None, 1, reaches_on=False,
                                                interrupt=interrupt)
            self.assertIsInstance(result, Inconclusive, interrupt)
            self.assertNotIn("signal", calls)
        late_lease = H1_UNARMED_EVENTS[:8] + [
            {"device_ms": 1224, "on": False, "reason": "lease", "sequence": 6, "renewed_ms": 864},
            {"device_ms": 1226, "on": False, "reason": "stop", "sequence": 7, "renewed_ms": 864}]
        result, calls, attempts = self.run_attempt(late_lease, H1_UNARMED_LOG, {"gate_pid": 1, "fault": None}, 1,
                                                   interrupt="pause", via_case=True)
        self.assertIsInstance(result, AssertionError)
        self.assertNotIsInstance(result, Inconclusive)
        self.assertIn("360ms", str(result))
        self.assertEqual(attempts, ["case"])


class ReportTest(unittest.TestCase):
    """main() of both harnesses, read from source; nothing is built or run."""

    def test_timing_reaches_report_json_also_when_a_case_fails(self):
        # The non-blocking timing section is set in the finally block of the try that runs the
        # cases, before report.json is written, so a failed case still records every attempt.
        assignment = ast.dump(ast.parse('report["timing"] = timing.payload()').body[0])
        for name in ("permit_verify.py", "bench_verify.py"):
            tree = ast.parse(Path(__file__).with_name(name).read_text())
            main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
            runs = [node for node in main.body if isinstance(node, ast.Try) and any(
                isinstance(call, ast.Call) and getattr(call.func, "id", None) == "host_case"
                for statement in node.body for call in ast.walk(statement))]
            self.assertEqual(len(runs), 1, name)
            final = runs[0].finalbody
            sets = [index for index, statement in enumerate(final) if ast.dump(statement) == assignment]
            writes = [index for index, statement in enumerate(final) if any(
                isinstance(node, ast.Constant) and node.value == "report.json" for node in ast.walk(statement))]
            self.assertEqual((len(sets), len(writes)), (1, 1), name)
            self.assertLess(sets[0], writes[0], name)


if __name__ == "__main__":
    unittest.main()
