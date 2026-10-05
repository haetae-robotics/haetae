# Gazebo exact-action controller permits (M3 reference)

This extends the H2 idea to the **actual Gazebo wheel and four-joint arm
controllers**. It is a same-host ROS 2 Jazzy / Gazebo Harmonic simulation
reference, not an MCU port, secure boot, hard realtime system or physical
robot protection result. UNO firmware is unchanged.

## Execution boundary

```text
signed world/VLA → Rust owner and permit signer (UID 2001)
                         ↓ exact signed Twist / trajectory / renewal
                 untrusted relay (UID 2005)
                         ↓ controller checks, never a generic motor enable
          PermitDiffDriveController / LeaseTrajectoryController
                         ↓ guarded wheel velocity / arm position writes
                    GazeboSimSystem → simulated robot
```

The authorizer owns the Rust subprocess, audit key and per-run Ed25519 permit
key. The relay owns only its restricted DDS credentials. Separate owner-only
principal trees and SROS2 permissions deny the relay authorizer/world/VLA keys,
authorized-output publication, guard-state publication and controller-manager
services. The root provisioner pins a public verification key in the controller
configuration. Every controller activation creates an unpredictable 128-bit
nonce; a key-owner restart cannot reset controller sequence state. Maintenance
test resets explicitly deactivate/reactivate at measured stop, rotate nonces
and discard queued work. They are not automatic operational recovery.

The controllers replace inherited command ingress. The base accepts signed
`TwistStamped` only, refuses chained mode and writes from the verified message.
The arm accepts the signed `FollowJointTrajectory` action only; direct trajectory
topic delivery stops it. Unsupported supplemental action/trajectory fields are
rejected. The gripper remains a trusted, fixed closed fixture.

## Permit contract

`header.frame_id` carries the reference permit for base commands and arm goals.
This is protocol metadata, not a TF frame. The reference relay forwards it without
restamping. A separate String topic carries arm renewals. The wire is bounded to
512 bytes, with canonical lowercase hex and decimal integers:

```text
v1:target-kind:activation_nonce:sequence:issued_sim_ns:issued_steady_ns:
expires_sim_ns:expires_steady_ns:payload_sha256:ed25519_signature
```

The signature domain is `haetae-controller-v1` followed by NUL. The canonical
binary digest includes the ROS header stamp and all supported command fields:
all six twist components (unused components must be zero), or exact ordered
joint names, point count, every point time and four positions. Velocity,
acceleration, effort, partial joints, extra goal tolerances and multi-DOF
motion are unsupported. Up to 16 points and a one-second arm chunk are accepted;
a first point at time zero is supported. Python/C++ fixed digest vectors check
the shared encoding.

Authorized arm goals use a zero ROS trajectory start stamp, bound in the digest,
so JTC begins at controller admission. Admission/cutoff checks use the signed
permit issue time for that case. The permit's original dual-clock issuance and
expiry are unchanged. A past JTC start time would otherwise compress the initial
segment after relay latency and could exceed the measured velocity policy.

Each target has a strictly increasing sequence shared by its ingress routes.
Trusted controller telemetry of the exact admitted digest and goal permit
sequence suppresses arm renewal
until admission; the relay's outer action acknowledgement is insufficient.
Successful completion retains that digest until an explicit stop/reset, so idle
renewal cannot cause a spurious binding rejection. An explicit reset clears the
owner's retained digest/goal identity too. Identical consecutive trajectories
have separate admitted goal identities, so old telemetry cannot renew a new goal.
Renewals bind the active trajectory digest, so an authenticated generic heartbeat
cannot authorize a different arm action. Admission is **under 50 ms** in both
ROS simulation time and same-host monotonic time. Expiry is anchored at issuance,
never receipt, at most **200 ms** and further bounded by Rust proposal/world
authority. The two processes require the same monotonic clock domain; this
protocol is not suitable for a separate physical controller as written.

Permit-mode enforcement ticks use a steady clock, so a slow Gazebo clock does
not stretch the renewal interval beyond the wall deadline. Each permit is also
bounded by the original request wall time of the latest strictly advancing
`world_updated` stamp that Rust accepted, and by that observation's ROS age.
Repeated stamps, rejected inputs and timer ticks cannot refresh this world
deadline. A frozen world therefore expires and disarms the owner even while
wall-clock ticks continue; recovery still requires an accepted explicit stop.

Invalid, replayed, delayed, expired or incorrectly targeted permits lock motion.
The wheel update forces zero without acceleration smoothing when authority is
lost. The arm holds measured positions, aborts/discards the old trajectory and
requires a new explicit zero/stop that the Rust owner accepted before rearming.
Fresh traffic or renewal alone cannot unlock either controller. Stop packets
cannot authorize motion. The reference uses mutexes and OpenSSL; physical stop
timing, scheduling bounds and worst-case processing costs are unqualified.

Locally generated `arm-stop` and exact zero `base-stop` revocations use the
current clocks of that new locking operation, including during asynchronous
cancellation or bridge failure. They retain the same under-50ms admission,
sequence/replay checks and maximum 200ms wire lifetime. They cannot unlock or
create motion authority. Goals, commands, leases and especially resets retain
the original Rust request clocks and remaining world/proposal budget; a delayed
response or cancel callback cannot refresh that positive authority.

## Verification

```bash
./haetae-demo verify
```

The secured Gazebo reference with `--attack-probes` additionally runs
`controller_probes.py`. Independent root test fixtures use the **real Rust
enforcer**, fresh signed measured-world inputs and the pinned permit signer.
Only public packets go over stdin to `controller_attack.py` running as relay
UID 2005 with the real relay enclave. Controller counters and actual Gazebo
odometry/joints, observed independently by the trusted harness, decide results.
Attacker acknowledgements do not decide pass/fail. Per-case root maintenance
activation/fixture reset is recorded in `setup.log`.

`controller-permits.json` records separate normal wheel/arm physical movement,
independent expiry, and unsigned, payload-altered, signature-altered, replayed,
delayed and cross-target commands for each controller. Negative cases require
an observed controller rejection, holding state, at most 0.02 m/rad motion drift
and no rearm from subsequent traffic. Native tests cover clock rollback, exact
expiry boundaries, stale activation nonces, oversized parser inputs and latch
recovery. Ordinary ROS/Gazebo scenarios still exercise the production
authorizer → relay → controller path, people revocation, tracking, cancellation
and authorizer kill/stall/delay faults.

## Remaining boundaries

Root, kernel, simulator, controller process/binary, pinned public configuration,
trusted observation source and Rust owner/signer remain trusted. The relay may
drop traffic or cause a stop; availability is not promised. A compromised
authorizer can sign dangerous commands. Signed observations authenticate source,
not physical truth. Full arm/tool geometry, persistent household effects,
separate-device clock/challenge protocols, real motor stop measurements and
hardware qualification remain separate work. No new protective release claim
is made by this reference.
