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
An update that already sampled a valid grant can finish its current control
cycle when rejection races with the write. Locking clears stored grants and
the following update holds/zeros; this is not an instantaneous callback stop
or an atomic hardware cutoff. The runtime tests measure the resulting stop.

Locally generated `arm-stop` and exact zero `base-stop` revocations use the
current clocks of that new locking operation, including during asynchronous
cancellation or bridge failure. They retain the same under-50ms admission,
sequence/replay checks and maximum 200ms wire lifetime. They cannot unlock or
create motion authority. Goals, commands, leases and especially resets retain
the original Rust request clocks and remaining world/proposal budget; a delayed
response or cancel callback cannot refresh that positive authority.

Before signing non-stop permits, the owner rechecks both original clock ages
and reserves the existing 50ms admission window within the original remaining
world/proposal budget. New commands/goals/resets with insufficient margin
revoke and disarm. For an already admitted arm goal, a timely, healthy renewal
round trip inside the original proposal's final admission reserve publishes
state only: no base command or arm lease is signed, and existing controller
permits retain their expiry. Rust still owns ordinary expiry, cancellation and
measured stop confirmation. Insufficient world margin or stale round trips
still revoke and disarm immediately. Expiry is never extended.
Non-reset arm renewal is also suppressed while trusted controller
telemetry reports holding; only an explicit accepted Rust stop can reset it.

World-budget admission cutoff has a distinct owner rejection from proposal TTL
or response delay. Disconnect and compound-fault evidence may use this earlier
world-authority cutoff only with a named rejection, disarmed engine state and
a subsequent observed zero command. Coverage loss still requires the signed
unknown-perception revocation; a generic admission-budget rejection cannot
stand in for either cause. Normal household arm completion can enter measured
arm settling before `no_command`; arbitrary denied states are not completion.

The household fixture harness has one explicit maintenance reset after initial
scene preparation, before any arm proposal is dispatched. It requires fresh
Rust-accepted semantic/world state, normal healthy engine state and measured
wheel/all-four-joint stop before each zero/stop request. The path is unavailable
after any arm proposal or verified denial and cannot recover a failed test.
Subsequent test rearm still requires ordinary completion or the expected
rejection verified by that test. This is fixture initialization, not production
automatic recovery.

This fixed fixture profile requires initial joint1 within 0.1 rad of zero. An
unexpected initial posture fails before any repositioning or setup reset; the
initializer cannot silently move an arm into its supported starting posture.

Each isolated arm kill/stall/delay fixture waits for newly published, rotated
wheel/arm activation challenges. Its existing ten-second owner startup wait
also requires the new owner's signed VLA subscription to report a matched
writer. A normal state and third-party graph counts can arrive before that
connection completes. `signed_vla_writers_matched` is a transport hint only;
it never grants or renews controller authority. Sources stay alive across owner
restarts, and signed VLA QoS remains reliable, volatile and depth one.
Post-publication stop IDs/counters, writer match events, and the owner's
`signed_vla_callbacks` count distinguish reservation, publication and ingress
when diagnosing a failure. These observations cannot replace Rust acceptance.
Owner startup failures also checkpoint that fixture's state, outcomes and log.

The subsequent barrier allows at most two sequential OFF-only
preparation requests within five seconds, to tolerate volatile DDS discovery
loss. This same deadline includes waiting for a newly advancing Rust-accepted
world and a subsequent fresh idle state before the first OFF request. Graph
discovery alone cannot satisfy this prerequisite. It neither arms a source nor
replaces the final authorization barrier; unchanged activation nonces and the
remaining deadline are checked before each OFF request.
Readiness can be transiently false because state and outcome use separate DDS
topics. Each OFF request waits for the same prerequisite inside the original
five-second deadline; this adds neither an OFF attempt nor a motion retry.
The exact final proposal ID must match the Rust-accepted stop; it also
requires a newer fresh idle/armed state, fresh wheel/arm controller reports
bound to the new nonces, an unlocked arm with a fresh idle lease, and measured
wheel/all-four-joint stop immediately
before dispatch. There is no repeating request producer or trailing stop after
the final accepted ID from the ordered signed VLA writer. The non-secure path
without the durable VLA counter allows only one preparation request. The barrier
avoids a fixed preparation sleep, which could hide a lapse beyond the 200 ms
permit lifetime.
Preparation emits no further stops or resets once the single arm goal is sent;
failure after dispatch cannot use this barrier to recover or retry motion.
The arm's idle lease must have a signed simulation origin strictly newer than
the controller reset cutoff. This accounts for conservative permit backdating
before the single goal is sent; it does not relax the controller cutoff or
retry a rejected goal.
The arm-only fixture permits a locked wheel controller when its current report
and measured linear and angular stop are fresh; it grants no wheel motion authority. General
rearm and scenes requiring wheel motion still require both controllers unlocked.

Telemetry keeps the total rejection counter, including invalid stop packets,
and a separate motion/reset/lease rejection counter. Normal arm completion
checks the latter: a rejected locking-only stop still locks and cannot be
interpreted as a denied positive action. Hostile probes keep requiring total
rejection plus fresh holding/drift evidence. Log messages name the ingress kind.

## Verification

The Gazebo observation node uses a single-threaded executor on its own spin
thread. Its callbacks already share the default mutually exclusive group, so
direct dispatch removes worker-pool handoffs without reducing callback
concurrency. Scenario waits and native sensor/pose transport retain their
separate threads. This reduces dispatch overhead; it is not a hard realtime
guarantee or proof of the cause of a previous qualification failure. Original
measurement timestamps, freshness limits and failure stops remain unchanged.

A complete joint observation also attempts publication of a newly advancing,
healthy fused world sample. This avoids waiting for the next 50 ms publication
tick after a new lidar/odom/joint measurement becomes available. The original
oldest measurement stamp is retained; an equal/older fused stamp cannot use
this additional path. The existing 50 ms timer still publishes sensor health
and semantic changes, including unknown coverage and repeated/frozen samples.
Neither path refreshes the original authority clock for a repeated world stamp.

The supported reference runs its controllers at 100 Hz. The trusted owner
conservatively backdates each permit's simulation origin and simulation expiry
by one 10 ms controller cycle (clamped at simulation zero). Its original wall
origin/expiry, original world observation, payload and sequence remain unchanged.
This covers a one-cycle difference between ROS clock reception and controller
updates by shortening simulation authority. It does not authorize a future
timestamp at the verifier, extend either expiry or change the 50/200 ms limits.
Larger clock skew still locks; frozen world and wall expiry still stop. Issuer
admission/headroom checks include this backdate, so they reject earlier too.
Other controller rates need a separately reviewed configuration; this is not a
general distributed-clock synchronization guarantee.
Zero-header arm goals still use the signed simulation origin against the
controller's reset/stop cutoff. A goal issued too soon after reset can therefore
be rejected; the backdate also leaves less than 40 ms nominal verifier admission
margin when the controller is ahead. These cases stop rather than retry motion.
Replay evidence requires the first packet to be admitted without a rejection,
then an identical packet hash to produce a fresh rejection without another
acceptance and the actual `sequence` rejection reason. Acceptance, activation,
manual stop/rejection and expiry replace previous diagnostic reasons, so a stale
sequence reason cannot qualify. A rejected first copy never qualifies as a pass.

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
delayed and cross-target commands for each controller, plus an arm renewal
signed 60 ms in the past while the arm moves under a live goal. That renewal
must be refused for `freshness`. The controller's own hold must begin after the
renewal left, before the goal's lease could end on either clock, and within
three controller updates of the update stamp on the first arm telemetry row
that shows the refusal. That row is published every 50 ms, so the check allows
a hold up to about eight updates after the refusal itself. From that row's
update, or from the hold if it came earlier, the arm may move at most 0.02 rad.
Negative cases require
fresh unlocked telemetry under the same nonce before delivery, no acceptance,
one new rejection with the expected binding/signature/sequence/freshness reason
observed before the prior lease's wall expiry (for the base, more than 20 ms
before it), the holding state that refusal latched (a latched wheel or arm
controller keeps reporting the refusal's reason, while an unlatched one would
report `expired` once the lease ends; the arm must also hold within three
controller updates of the first telemetry row that shows the refusal and move
at most 0.02 rad from it, as above), at most 0.02 m/rad motion drift and no
rearm from subsequent traffic. A refusal that reaches a controller whose lease
already lapsed replaces `expired` with its own reason, so a lapse published
before the refusal is judged like any other lock (below). Native tests cover
clock rollback, exact expiry boundaries, stale activation nonces, oversized parser
inputs and latch recovery. Ordinary ROS/Gazebo scenarios still exercise the production
authorizer → relay → controller path, people revocation, tracking, cancellation
and authorizer kill/stall/delay faults.
The stale-packet case signs deliberately older origins rather than waiting for
the controller reset lease to expire. It signs them when the packet is sent,
with a full 200 ms lease, so the permit's own ends are still ahead and only the
50 ms age bound can refuse it; a refusal observed too close to those ends is
not counted. An expired reset or a generic locked-arm precheck cannot count as
a successful property-specific negative control.

Shared-runner delay can make a probe attempt inconclusive but never makes it
pass. The harness signs nothing when the fixture issuer times out or its
approval arrives late, expired or with no authority left. It does not send a
positive permit that the verifier's own 50 ms predicate would already refuse,
an arm goal signed before the controller's last hold cutoff, or any positive
permit once the last lease it sent has ended. Positive permits leave one at a
time, so each refusal is observed on its own. A delay in the harness itself, in
a positive case or before a negative packet leaves, counts as inconclusive only
after the first refusal or lock the controller already published was judged.
Before the packet of a negative case leaves (for `replay`, its first copy),
only the passing of time may show at the controller; before the replay's
duplicate and the late renewal leave, only that first refusal or lock is
judged. Once the motion threshold is reached, the harness still waits for
the controller to admit or refuse the last permit it sent, or for the current
lease to lapse, and judges that outcome too. An admission count past the
permits the harness sent then fails at once, unless an inconclusive refusal or
lock, such as a `freshness` refusal, already ended the attempt while the
harness waited; that attempt is retried, never passed.
Beyond these harness-side delays, an attempt is inconclusive only in these
cases:

- a permit that was timely when it left was refused for `freshness`;
- a lock (`expired`, `locked`, or `rejected` at the arm action ingress) that
  the controller's own telemetry places at or after the end of the lease it
  relied on, including a lapse published before a negative packet's refusal
  (an arm goal must still be counted, below).
  The controller's admission counter identifies that lease. For the arm the
  evidence is the hold it stamps: at or after the wall end, or within one 10 ms
  update of the simulation end, because the controller may notice a lapse in a
  callback before its next update. For the base it is the telemetry
  publication, which may come at most 20 ms before the signed wall end: the
  10 ms simulation backdate plus one 10 ms update, assuming the controller's
  simulation clock leads the harness's view by at most one update. A larger
  lead makes a real lapse look early, which fails a case whose lapse is
  published rather than passing it; a lapse that a refusal overwrites before
  the next 20 ms row stays invisible, and that case can then pass without
  showing that the refusal itself latched. Because that telemetry is
  published every 20 ms, the lock itself may come up to about 40 ms before
  that end;
- an arm goal that the verifier admitted and the action handshake then refused,
  with the controller's hold stamped no more than one 10 ms update before the
  goal's own 50 ms simulation window closed;
- a live-lease window that closed before the packet of a negative case left
  (for `replay`, its first copy), when nothing but time changed at the
  controller: the same nonce and counters, and any `expired` lock placed at or
  after the lease end by the same evidence;
- in the late-renewal case, a goal that was not admitted, or not seen moving
  the arm more than 0.005 rad while the goal's own lease was live, within
  0.2 s of the goal's send and with a telemetry row under 50 ms old. This is
  inconclusive only once the controller counted the goal (below) and, read
  after that count, had admitted at most one packet (only the goal was sent),
  and once the first refusal or lock published since the reset was judged as
  in this list. A refusal of the goal that comes after a lapse is judged by
  its reason (below);
- a property-specific rejection observed only after that lease ended (for the
  base, within 20 ms of its wall end, where a lapse may come first and the
  refusal's reason then replaces `expired`) or too close to a stale permit's
  own end, a latched arm refusal whose hold stamps reach the end of the lease
  the arm relied on, and an admitted replay goal's own motion before the
  duplicate's refusal.

Even then, once a packet that must be refused has left, the robot must not have
moved: the wheel or arm drift check runs before any attempt counts as
inconclusive. The case then restarts from a fresh maintenance reset, at most
three attempts; three inconclusive attempts fail CI. An occasional premature
lock that falls inside these windows therefore looks like runner delay: it
stops the robot early and fails CI only when all three attempts are
inconclusive. These fail at once:
admitting a packet that must be refused, a rejection counted before it left,
any other rejection reason, a lock before such a bound, an arm hold more than
three updates after the first telemetry row that shows its refusal, motion over
0.02 m or rad, and automatic recovery. A harness delay while the recovery
packet is prepared counts as inconclusive only while the controller still holds
with the same nonce and counters.

A packet that the controller never counts is judged by how it travels. Arm
goals use a reliable action whose ingress counts every goal, even one that
arrives after a lapse (as `rejected`), so on every path, inconclusive ones
included, an arm goal that the controller does not count fails. For a valid
goal (the positive control's, the late-renewal case's or the arm replay's
first copy) the harness waits at least one second after its send (the
probe's admission wait for a valid goal) for its admission or refusal. That
refusal must give a reason a correct controller gives a valid goal: `rejected`
at the action ingress or handshake, `freshness`, or `locked` when the
controller's lease lapsed as the goal arrived. Any other reason fails, even
when an earlier lapse at that lease's end made the attempt inconclusive. For a
goal that must be refused it waits at least two seconds, as the conclusive
path does, for that goal's counted refusal and a hold no later than three
updates after the first telemetry row that shows it. A late renewal that the
controller has not counted by then fails too, even when the goal's lease then
lapses at its end, because no refusal and hold bound the arm's motion; the
harness cannot tell a renewal that its best-effort topic lost from one the
controller ignored. A recovery packet that the latched controller does not
count within two seconds fails; the base recovery command travels on the
best-effort command topic, so like a lost late renewal it can fail CI without
a controller fault. Other packets that the controller never counts are
retried once the lease they relied on lapses at its end: a base packet that
must be refused, a base command or arm renewal that should be admitted
(including the base replay's first copy), and either maintenance-reset packet
once the other controller's reset lease lapses (if both are lost, the reset
times out and fails). These travel on best-effort topics. Such an attempt
cannot pass, because it lacks the refusal, admission or motion it needs, but a
controller that only sometimes ignores these packets fails CI only when all
three attempts are inconclusive. A refusal that the controller never shows
latched fails when its two-second wait ends. Each check row records the
attempt that passed.

CI non-blocking measurement: `controller-timing.json` records, per run, each
live renewal round trip, the identical world-admission step of every approval,
the approval round trip, the age that the verifier would compute for each
permit at the harness's pre-send freshness check (positive permits, reset
packets, the signature and replay negatives and the base recovery packet),
including permits withheld there because they were already 50 ms old, and every
attempt with its outcome. Attempts are recorded for the 15 cases that may be
retried; the base and arm expiry checks run once, outside the retry loop, so
they are not in `controller-timing.json`; their rows in
`controller-permits.json` and `result.json` carry attempt 1. A CI step
reports nearest-rank p50/p95/max for four rows: `renewal_path_ms` (the live
renewals pooled with the world-admission steps, which have no row of their
own), `renewal_ms`, `actuation_admission_ms`
and `send_age_ms`. It emits a warning when the p95 of a row with at least 20
samples reaches 50 ms, and only a notice when a row with fewer samples does. Any
inconclusive attempt also emits a warning. Other notices flag a row without
samples, a pooled renewal row under 20 samples, an incomplete run, and a
missing or unreadable record. The job summary also lists every attempt that did
not pass: each inconclusive attempt with its stage and cause and, when a case
failed, its failure with the error, or `all_attempts_inconclusive` when none of
its attempts was conclusive. The step always exits 0, so the measurement never
fails a check. It is not a latency claim, and the 50 ms and 200 ms verifier
limits are unchanged.

## Remaining boundaries

Root, kernel, simulator, controller process/binary, pinned public configuration,
trusted observation source and Rust owner/signer remain trusted. The relay may
drop traffic or cause a stop; availability is not promised. A compromised
authorizer can sign dangerous commands. Signed observations authenticate source,
not physical truth. [M2a history](household-history.md) preserves observations and pure-motion steps;
verified persistent household effects and full arm/tool geometry,
separate-device clock/challenge protocols, real motor stop measurements and
hardware qualification remain separate work. No new protective release claim
is made by this reference.
