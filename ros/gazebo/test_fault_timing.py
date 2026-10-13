"""The shared-runner fault timing summary is a measurement only: it never fails CI."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fault_timing import main
from public_report import report
from test_public_report import fault_result

HERE = Path(__file__).resolve().parent


def run(data, raw=None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "verification-report.json"
        summary = Path(directory) / "summary.md"
        if raw is not None:
            path.write_text(raw)
        elif data is not None:
            path.write_text(json.dumps(data))
        out = io.StringIO()
        code = main(["fault_timing.py", str(path)], {"GITHUB_STEP_SUMMARY": str(summary)}, out)
        return code, out.getvalue(), summary.read_text() if summary.exists() else ""


class FaultTimingSummaryTest(unittest.TestCase):
    def test_samples_inside_their_limits_report_without_warning(self):
        code, out, summary = run(report(fault_result()))
        self.assertEqual(code, 0)
        self.assertNotIn("::warning", out)
        self.assertIn("| sensor_coverage | fault_to_zero_wall_ms | 147.6 | 400 | yes |", summary)
        self.assertIn("| base_deadman | gate_kill_to_base_stop_wall_ms | 261.4 | 3000 | yes |", summary)

    def test_slow_runner_and_retries_warn_but_exit_zero(self):
        value = fault_result()
        value["sensor_faults"]["coverage"].update(fault_to_zero_wall_ms=436.8, attempt=2)
        code, out, summary = run(report(value))
        self.assertEqual(code, 0)
        warnings = [line for line in out.splitlines() if line.startswith("::warning")]
        self.assertEqual(warnings, [
            "::warning title=Fault timing (CI non-blocking measurement)::sensor_coverage fault_to_zero_wall_ms: "
            "436.8 ms, over its 400 ms limit on this shared runner",
            "::warning title=Fault timing (CI non-blocking measurement)::sensor_coverage: 1 inconclusive attempt(s) "
            "before the pass, never counted as a pass"])
        self.assertIn("| sensor_coverage | fault_to_zero_wall_ms | 436.8 | 400 | no |", summary)

    def test_controller_permit_retries_are_left_to_the_permit_timing_step(self):
        value = report(fault_result())
        permits = next(row for row in value["checks"] if row["id"] == "controller_permits")
        permits["measurements"]["inconclusive_attempts"] = 2
        code, out, _ = run(value)
        self.assertEqual(code, 0)
        self.assertNotIn("::warning", out)

    def test_failed_missing_or_garbage_report_is_a_notice_and_exit_zero(self):
        for data, raw in ((report(None, failed=True), None), (None, None), (None, "{not json"), ([1], None),
                          ({"checks": [{"timing": "x"}, 3, {"timing": [5]}]}, None)):
            code, out, _ = run(data, raw)
            self.assertEqual(code, 0)
            self.assertNotIn("::error", out)
            self.assertIn("::notice", out)

    def test_cli_exits_zero_for_missing_file(self):
        result = subprocess.run([sys.executable, str(HERE / "fault_timing.py"), "/nonexistent/verification-report.json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("::notice", result.stdout)


if __name__ == "__main__":
    unittest.main()
