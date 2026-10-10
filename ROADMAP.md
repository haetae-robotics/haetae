# Roadmap

As of 2026-10-10. Haetae is pre-alpha and not safety-rated.

NOW lists the approved current work. Only the maintainer changes the NOW, NEXT, THEN and FROZEN lists.
AI coding agents follow [AGENTS.md](AGENTS.md).

## NOW

- **NOW-1: CI reliability.** Pin runner images before the `ubuntu-latest` migration, keeping required check names.
  Gazebo permit probes test fail-closed behavior, not shared-runner timing; timing becomes a non-blocking measurement.
- **NOW-2: Public wording.** One English tagline across the README, package metadata and simulator page; remove
  overclaims; a README quickstart that actually runs today.
- **NOW-3: Roadmap and agent rules.** This file and [AGENTS.md](AGENTS.md), the rules for AI coding agents.
- **NOW-4: Evidence labels.** Mark the household report's three judge-only diagnostics (verdict only, no measured
  motion), note that all six hazard cases share one geometry, and state the H2 scope (same-UID host, visual LED check).

## NEXT

Planned. Work starts only after the maintainer moves an item to NOW.

- **Per-tick setpoint admission, shadow/audit mode only.** Judge each joint setpoint per control tick (joint limits,
  maximum step from the measured position, FK workspace box, freshness) and record verdicts without blocking motion.
  Includes an SO-101 arm profile and an audit report over public recorded rollouts. No change to signed permit,
  MAC or lease code and no enforcement mode.
- **Shadow/audit-only pip alpha (`0.1.0a1`)**, replacing the `0.0.1` placeholder packages.
- **Haetae Permit Protocol (HPP) v0.1 spec draft** for permits checked on a separate physical controller, with test
  vectors and an external human review of the spec before any firmware.
- **CI hardening.** Pin GitHub Actions to full commit SHAs and declare the minimum supported Rust version (MSRV).

## THEN

Later steps that need hardware.

- **Single-servo bus-guard spike (go/no-go).** One servo behind a permit-checking board that forwards only permitted
  bus commands.
- **Only if the spike passes and the maintainer decides to continue:** H3 firmware (HPP on a real arm), then a
  preregistered physical measurement (plan and pass criteria fixed before measuring), published with raw data.

## FROZEN

Bug fixes only, for 90 days from 2026-10-10.

- Expanding the household hazard lab or durable household history, including new semantic hazard tables and
  reference perception observers.
- In-house implementation of M4 (full arm/tool geometry) and M5 (task-level checks).
- Timing polish in the same-host Gazebo M3 reference; it stays as a regression test.
- Visual brand work: logo, colors and icons (`docs/brand.md`, `sim/brand/`). The NOW-2 tagline and README wording
  are not brand work. Renaming is not frozen; keeping or changing the name is the maintainer's decision.

Exception: test pass conditions, labels and CI configuration that NOW-1 or NOW-4 needs, or that the maintainer
approves for another NOW item, are allowed. They still add no features, no new hazard scenarios and no change to the
50 ms / 200 ms timing limits.

## Not in scope

Haetae is meant to complement a robot's certified safety functions, not replace them. It is not:

- A certified safety function
- An emergency stop (E-stop) or Safe Torque Off (STO)
- A perception system or semantic hazard model
- Collision checking or motion planning
- An evaluation harness
