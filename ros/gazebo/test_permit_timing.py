"""The shared-runner timing summary is a measurement only: it never fails CI."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from permit_timing import annotation, main, nearest_rank, summarize

HERE = Path(__file__).resolve().parent


def run(data, raw=None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "controller-timing.json"
        summary = Path(directory) / "summary.md"
        if raw is not None:
            path.write_text(raw)
        elif data is not None:
            path.write_text(json.dumps(data))
        out = io.StringIO()
        code = main(["permit_timing.py", str(path)], {"GITHUB_STEP_SUMMARY": str(summary)}, out)
        return code, out.getvalue(), summary.read_text() if summary.exists() else ""


class TimingSummaryTest(unittest.TestCase):
    def test_nearest_rank_percentiles(self):
        values = list(range(1, 21))
        self.assertEqual((nearest_rank(values, .5), nearest_rank(values, .95), max(values)), (10, 19, 20))
        self.assertEqual(nearest_rank([7.0], .95), 7.0)

    def test_fast_runner_reports_without_warning(self):
        data = {"complete": True, "series": {"renewal_ms": [5.0] * 5, "world_admission_ms": [8.0] * 40,
                                             "actuation_admission_ms": [3.0] * 40, "send_age_ms": [12.0] * 30}}
        code, out, summary = run(data)
        self.assertEqual(code, 0)
        self.assertNotIn("::warning", out)
        self.assertIn("| renewal_path_ms | 45 | 8.0 | 8.0 | 8.0 |", summary)

    def test_slow_runner_warns_but_exits_zero(self):
        data = {"complete": True, "series": {"renewal_ms": [61.0] * 3, "world_admission_ms": [55.0] * 30},
                "attempts": [{"case": "arm_positive", "attempt": 1, "outcome": "inconclusive",
                              "stage": "arm renewal", "evidence": {"cause": "renewal_round_trip_at_or_over_limit"}},
                             {"case": "arm_positive", "attempt": 2, "outcome": "passed"}]}
        code, out, summary = run(data)
        self.assertEqual(code, 0)
        self.assertIn("::warning title=Permit timing (CI non-blocking measurement)::renewal_path_ms p95", out)
        self.assertIn("1 inconclusive probe attempt(s)", out)
        # Three live renewals alone cannot carry a p95 warning.
        self.assertIn("::notice title=Permit timing (CI non-blocking measurement)::renewal_ms p95", out)
        self.assertIn("| arm_positive | 1 | inconclusive | arm renewal | renewal_round_trip_at_or_over_limit |", summary)

    def test_an_inconclusive_attempt_alone_is_a_warning(self):
        data = {"complete": True, "series": {"renewal_ms": [5.0] * 3, "world_admission_ms": [8.0] * 30,
                                             "actuation_admission_ms": [3.0] * 30, "send_age_ms": [12.0] * 30},
                "attempts": [{"case": "base_delay", "attempt": 1, "outcome": "inconclusive", "stage": "base delay",
                              "evidence": {"cause": "rejection_not_separable_from_lease_end"}},
                             {"case": "base_delay", "attempt": 2, "outcome": "passed"}]}
        code, out, _ = run(data)
        self.assertEqual(code, 0)
        self.assertEqual([line for line in out.splitlines() if line.startswith("::warning")],
                         ["::warning title=Permit timing (CI non-blocking measurement)::1 inconclusive probe attempt(s) "
                          "retried from reset, never counted as a pass: base_delay#1"])

    def test_missing_or_garbage_record_is_a_notice_and_exit_zero(self):
        for data, raw in ((None, None), (None, "{not json"), ([1, 2], None), ({"series": {"renewal_ms": "x"}}, None),
                          ({"series": {"renewal_ms": [float("nan"), -1, True]}}, None)):
            code, out, _ = run(data, raw)
            self.assertEqual(code, 0)
            self.assertNotIn("::error", out)
            self.assertIn("::notice", out)

    def test_incomplete_run_and_annotation_escaping(self):
        rows, notes = summarize({"series": {"renewal_ms": [1.0]}})
        self.assertIn(("notice", "controller probes did not complete; samples cover the run until it stopped"), notes)
        self.assertIn(("notice", "renewal_path_ms: only 1 samples (< 20)"), notes)
        # A failed run also lists its failed attempt, which bounded_attempts() never retried.
        failed = {"case": "base_delay", "attempt": 1, "outcome": "failed", "stage": "AssertionError",
                  "evidence": {"error": "base delay moved under invalid permit"}}
        code, _, summary = run({"series": {"renewal_ms": [1.0]}, "attempts": [failed]})
        self.assertEqual(code, 0)
        self.assertIn("| base_delay | 1 | failed | AssertionError | base delay moved under invalid permit |", summary)
        self.assertEqual(annotation("warning", "a%b\nc"),
                         "::warning title=Permit timing (CI non-blocking measurement)::a%25b%0Ac")
        self.assertNotIn(":", annotation("notice", "x").split("::")[1].split("title=")[1])

    def test_cli_exits_zero_for_missing_file(self):
        result = subprocess.run([sys.executable, str(HERE / "permit_timing.py"), "/nonexistent/controller-timing.json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("::notice", result.stdout)


if __name__ == "__main__":
    unittest.main()
