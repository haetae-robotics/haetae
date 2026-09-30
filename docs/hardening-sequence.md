# Simulator hardening sequence

Authorized scope (2026-10-01): implement, validate, commit, independently review,
check exact-head CI, and merge each of these three stages.

## 1. Independent arm stop and failure tests

- A ros2_control arm controller must hold measured joint positions when fresh
  gateway heartbeats disappear, even if Python or Rust is killed or stalled.
- Enforce a 250 ms lease against both simulator and monotonic wall time;
  reject stale, duplicate and future heartbeat renewal.
- Discard the old trajectory on expiry. Recovery cannot resume that trajectory.
- Exercise kill, Rust stall and heartbeat delay during actual Gazebo arm motion;
  record all joint motion and causal controller stop events. Keep existing
  base/person/action/attack checks and current freshness/trajectory budgets.
- Present the fault stop in the live scene; do not claim hardware stopping.

## 2. Separate keys and least-privilege roles

- Split role signing from the gateway: perception owns world/fault authority,
  AI proposal ingestion owns VLA authority, and the gateway only verifies.
- Give each process a separate OS identity and minimal SROS2 permissions.
- The gateway and VLA principals must fail to read perception signing keys or
  inject world/fault/controller/heartbeat messages outside their authority.
- Require positive controls as well as denied attacks; retain replay, epoch,
  exact-payload and failure-stop guarantees. Document trusted provisioner and
  host/simulator boundaries explicitly.

## 3. Native Gazebo person and measured sensor path

- Put the moving person into the actual Gazebo world and both views at the
  same world pose, with physical geometry visible to a simulated sensor.
- Feed detection/range from that sensor through the isolated perception signer.
  Scenario path generation must not directly populate the security world.
- Compare detection against ground truth only in the test harness. Test
  approach/departure, stale sensor feed and detection failure. Missing or stale
  measurements must not become an invented empty/safe scene.
- Keep demo pacing, person clearance, measured robot motion, and clear labels
  distinguishing simulator sensor data from physical/hardware evidence.

## Review and merge contract

Each stage uses its own branch/PR from current main. Record exact base/head,
patch hash, code-reviewer output, architect output and CI evidence under local
`.omx/artifacts/`. Material fixes require both complete review lanes again.
Do not commit runtime logs or credentials. Reference validation does not close
real-robot or certification requirements in `security-release.md`.
