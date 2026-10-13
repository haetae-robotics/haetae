#!/usr/bin/env python3
"""Summarize shared-runner fault timing. CI non-blocking measurement.

Reads the verification-report.json that run_reference.py writes and lists every
timing sample that public_report.report() recorded against its unchanged limit
(fault to zero 400 ms; arm lease age 100/150 ms and hold 400/320 ms, wall/sim;
gate kill to base stop 3000 ms). A sample over its limit, and lost coverage that
passed only after inconclusive attempts, give a warning about this shared runner,
never a verdict: the step always exits 0, and no limit is changed. A failed run
writes no samples; its evidence is in error.json and the job log.
"""
import json
import os
import sys

from permit_timing import escape

TITLE = "Fault timing (CI non-blocking measurement)"


def annotation(level, message):
    return "::" + level + " title=" + escape(TITLE, True) + "::" + escape(message)


def summarize(report):
    """Return (table rows, [(level, message)]) for one verification report."""
    rows, notes = [], []
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    for check in (row for row in checks if isinstance(row, dict)):
        for entry in check.get("timing") if isinstance(check.get("timing"), list) else []:
            if not isinstance(entry, dict):
                continue
            row = (check.get("id"), entry.get("name"), entry.get("value_ms"), entry.get("limit_ms"),
                   "yes" if entry.get("within_limit") is True else "no")
            rows.append(row)
            if row[4] == "no":
                notes.append(("warning", "%s %s: %s ms, over its %s ms limit on this shared runner" % row[:4]))
        # Lost coverage only: permit_timing.py already reports controller-permit retries.
        measurements = check.get("measurements") if isinstance(check.get("measurements"), dict) else {}
        retried = measurements.get("inconclusive_attempts") if check.get("id") == "sensor_coverage" else None
        if type(retried) is int and retried > 0:
            notes.append(("warning", "%s: %d inconclusive attempt(s) before the pass, never counted as a pass"
                          % (check.get("id"), retried)))
    if not rows:
        notes.append(("notice", "no timing samples recorded (report status: %s)" % report.get("status")))
    return rows, notes


def render(rows):
    lines = ["### Shared-runner fault timing (CI non-blocking measurement)", "",
             "Single timing samples compared with their unchanged limits. These numbers never fail CI.", "",
             "| check | sample | value ms | limit ms | within limit |", "|---|---|---:|---:|---|"]
    lines += ["| " + " | ".join(str(cell).replace("|", "/")[:80] for cell in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def main(argv, environ=os.environ, out=sys.stdout):
    path = argv[1] if len(argv) > 1 else "artifacts/gazebo-reference/verification-report.json"
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
        if not isinstance(report, dict):
            raise ValueError("report is not an object")
        rows, notes = summarize(report)
        for level, message in notes:
            print(annotation(level, message), file=out)
        text = render(rows)
        print(text, file=out)
        summary = environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text)
    except Exception as exc:  # A measurement must never become a CI verdict.
        print(annotation("notice", "no usable report at %s: %s: %s" % (path, type(exc).__name__, exc)), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
