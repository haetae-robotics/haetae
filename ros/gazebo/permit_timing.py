#!/usr/bin/env python3
"""Summarize shared-runner controller-permit timing. CI non-blocking measurement.

Reads controller-timing.json written by controller_probes.py, prints nearest-rank
p50/p95/max for each row of SERIES (world_admission_ms appears only inside the
pooled renewal_path_ms), warns when the p95 of a row with at least MIN_SAMPLES
samples reaches the 50 ms verifier window (a smaller row gets a notice), warns
about inconclusive probe attempts, and lists the attempts that did not pass
(each inconclusive attempt and, when a case failed, its failure, including a
case whose attempts were all inconclusive). It always exits 0: these numbers
describe the shared runner and never decide a required check. The controller's
50 ms and 200 ms limits are unchanged.
"""
import json
import math
import os
import sys

TITLE = "Permit timing (CI non-blocking measurement)"
LIMIT_MS = 50.0
MIN_SAMPLES = 20
# renew() and approve() both time: origin -> fresh measured feedback ->
# signed world update accepted by Rust (the same statements).
SERIES = (("renewal_path_ms", ("renewal_ms", "world_admission_ms")),
          ("renewal_ms", ("renewal_ms",)),
          ("actuation_admission_ms", ("actuation_admission_ms",)),
          ("send_age_ms", ("send_age_ms",)))


def nearest_rank(values, quantile):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def clean(raw):
    if not isinstance(raw, list):
        return []
    return [value for value in raw if type(value) in (int, float) and math.isfinite(value) and value >= 0]


def escape(text, prop=False):
    text = str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


def annotation(level, message):
    return "::" + level + " title=" + escape(TITLE, True) + "::" + escape(message)


def summarize(data, limit_ms=LIMIT_MS):
    """Return (table rows, [(level, message)]) for one timing record."""
    raw = data.get("series") if isinstance(data.get("series"), dict) else {}
    rows, notes = [], []
    for name, sources in SERIES:
        values = [value for source in sources for value in clean(raw.get(source))]
        if not values:
            notes.append(("notice", name + ": no samples recorded"))
            continue
        p50, p95, high = nearest_rank(values, .5), nearest_rank(values, .95), max(values)
        rows.append((name, len(values), p50, p95, high))
        if name == "renewal_path_ms" and len(values) < MIN_SAMPLES:
            notes.append(("notice", "%s: only %d samples (< %d)" % (name, len(values), MIN_SAMPLES)))
        if p95 >= limit_ms:
            # Below MIN_SAMPLES the p95 is just the maximum: report it, do not warn.
            notes.append(("warning" if len(values) >= MIN_SAMPLES else "notice",
                          "%s p95 %.1f ms >= %g ms on this shared runner (n=%d, p50 %.1f, max %.1f)"
                          % (name, p95, limit_ms, len(values), p50, high)))
    attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    inconclusive = [row for row in attempts if isinstance(row, dict) and row.get("outcome") == "inconclusive"]
    if inconclusive:
        notes.append(("warning", "%d inconclusive probe attempt(s), never counted as a pass: %s"
                      % (len(inconclusive), ", ".join(sorted({"%s#%s" % (row.get("case"), row.get("attempt"))
                                                              for row in inconclusive})))))
    if data.get("complete") is not True:
        notes.append(("notice", "controller probes did not complete; samples cover the run until it stopped"))
    return rows, notes


def render(rows, data, limit_ms=LIMIT_MS):
    lines = ["### Shared-runner permit timing (CI non-blocking measurement)", "",
             "Nearest-rank percentiles compared with %g ms. These numbers never fail CI. "
             "renewal_path_ms pools live renewals with the identical world-admission step of each approval."
             % limit_ms, "", "| series | n | p50 ms | p95 ms | max ms |", "|---|---:|---:|---:|---:|"]
    lines += ["| %s | %d | %.1f | %.1f | %.1f |" % row for row in rows]
    attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    # Every attempt that did not pass: each inconclusive one and a failed case's failure.
    not_passed = [row for row in attempts if isinstance(row, dict) and row.get("outcome") != "passed"]
    if not_passed:
        lines += ["", "| case | attempt | outcome | stage | cause |", "|---|---:|---|---|---|"]
        for row in not_passed:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            cells = (row.get("case"), row.get("attempt"), row.get("outcome"), row.get("stage"),
                     evidence.get("cause", evidence.get("error", "")))
            lines.append("| " + " | ".join(str(cell).replace("|", "/").replace("\n", " ")[:120]
                                           for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv, environ=os.environ, out=sys.stdout):
    path = argv[1] if len(argv) > 1 else "artifacts/gazebo-reference/controller-timing.json"
    try:
        limit_ms = float(argv[2]) if len(argv) > 2 else LIMIT_MS
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("timing record is not an object")
        rows, notes = summarize(data, limit_ms)
        for level, message in notes:
            print(annotation(level, message), file=out)
        text = render(rows, data, limit_ms)
        print(text, file=out)
        summary = environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            # Write the table directly; redirecting stdout would hide annotations.
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text)
    except Exception as exc:  # A measurement must never become a CI verdict.
        print(annotation("notice", "no usable timing record at %s: %s: %s" % (path, type(exc).__name__, exc)),
              file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
