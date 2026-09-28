# Security release gate

Status: **not ready for a protective deployment or public security claim**.
This page records exactly which attack paths are covered by code and which
still need evidence. Haetae is a supervisory control layer, never a certified
safety function or a general network intrusion prevention system.

## Protected boundary

The intended deployment has three distinct processes or principals:

1. Untrusted VLA/planner processes publish proposals to source-specific ROS 2
   topics. They have no permission to publish world, fault, `/cmd_vel`, or arm
   controller goals.
2. A trusted perception and health path publishes world and faults. The Haetae
   ROS bridge binds each input topic to a role, signs its JSON payload with a
   different Ed25519 key per role, and supplies trusted receive time.
3. The Rust enforcer verifies the signed trust bundle, exact policy hash,
   input signature, role, epoch and increasing counter before judgment. Only
   the ROS bridge publishes `/cmd_vel` and submits arm action goals. The base
   and arm controllers independently stop or cancel on lost bridge input.

The bridge, its key files, the OS account, the trusted perception path, the
controller configuration and the SROS2 keystore are in the trusted computing
base. A compromised bridge or privileged host can still command the robot.

## Attack coverage

| Attack | Current code | Evidence required before release |
|---|---|---|
| Compromised model proposes overspeed or forbidden base path | Disc sweep, braking horizon, speed/turn clamp; denials output zero | ROS kinematic scenarios, measured controller deceleration and latency |
| Model continues after a human appears | Every world update and tick rechecks the active velocity; revoke disarms until a zero command | ROS end-to-end latency and minimum-gap measurements |
| Arm chunk exceeds joint bounds or rate | Short joint trajectory validates start, position, velocity, acceleration, final rest and tracking | Joint controller action integration, cancellation and actual controller interpolation |
| Human appears while arm moves | Arm command is cancelled and remains disarmed until two stationary world samples | ROS action cancellation test and independent hardware stop test |
| Forged model, world or fault JSON | Role-bound Ed25519 input signatures; source must match signing role | SROS2 `Enforce` test with unauthorized nodes and protected key files |
| Replayed signed message | Per-role monotonic counter and persisted epoch/checkpoint | Crash/restart/replay tests across operator reset and key rotation |
| Policy or trust file changed | Root-signed trust bundle binds exact policy bytes | Provisioning and rollback test on target host |
| Gateway dies or its response stalls | ROS bridge publishes zero on failure; base deadman is required | Kill -9 tests for bridge and Rust child against configured controllers |
| Rogue direct `/cmd_vel` publisher | ROS graph count raises Hold after discovery | SROS2 permissions must prevent the publisher from connecting |
| Privileged host compromise, key theft, bad perception, unsafe physics | **Not prevented** | Separate host hardening, key protection, sensor validation and certified safety layer |

## Current implementation limits

- The Rust core and subprocess tests run locally. The ROS 2 node has not yet
  been exercised against Jazzy, a differential controller or an arm controller.
- The arm check covers joint space. It does not compute 3D link geometry,
  self-collision, singularities, torque, force, payload or contact. Arm motion
  is denied when any human is present in the current world, but absence of a
  human report is not a safety proof.
- Every arm chunk is at most the policy's `max_duration_ms`; the controller
  must cancel on a request and independently stop on gateway death. A generic
  `joint_trajectory_controller` action alone is not proof of this property.
- SROS2 permissions and OS isolation have not been deployed or attacked in
  CI. Publisher counting detects a discovered extra writer; it cannot prevent
  a command that reaches the controller first.
- The bridge holds all configured signing seeds in one Python process. A
  bridge compromise can forge every configured role. Use separate OS accounts
  and enclaves for the source and trusted world paths; move key operations to
  isolated signers before a protective release.
- Signed counter checkpoints are fsynced after each output line. A crash in
  that small interval may lose the last checkpoint. The unclean-start Hold
  blocks motion until an offline operator reset, but replay after that reset
  needs an explicit adversarial test.
- No hardware or certification evidence exists. Controller limits in policy
  must match measured hardware limits. The system must remain behind the
  robot's independent certified safety functions.

## Release blockers

All of these are required before claiming that Haetae protects a robot from
untrusted or compromised AI commands:

1. Eight ROS 2 end-to-end base scenarios from `docs/w3-plan.md`, including
   revoke, stale perception, process kill and restart; five repeat runs on the
   selected Jazzy/RMW/controller combination with attached logs and traces.
2. A configured arm controller and independent stop path; action chunk
   replacement, cancellation, kill and joint-tracking tests with measured
   latency and position error. A robot-specific 3D collision model is required
   before claiming spatial arm protection.
3. SROS2 `Enforce` on the actual graph. An unauthorized participant must fail
   to publish `/cmd_vel`, world, fault or arm goals. Check the effective
   permissions, not just the policy file.
4. Host deployment with a dedicated bridge account, owner-only signing seeds,
   a pinned root public key, immutable policy/trust deployment and documented
   offline reset/key rotation.
5. Independent security review of the immutable release candidate, adversarial
   tests for malformed inputs, signature/key substitution, replay and log/state
   tampering, and a repeat review after any material fix.
6. Public security contact, supported-version policy, reproducible build,
   signed release artifacts, install guide and a claim limited to demonstrated
   robots and attack paths.

No release artifact or registry version should be labelled protective while
one of these blockers is open.
