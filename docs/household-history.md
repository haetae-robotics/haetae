# Durable household observations and motion history (M2a)

This is a bounded simulator/reference increment. It preserves trusted observed
container contents and consumes pure arm-motion steps across an enforcer restart.
It is **not the complete M2 effect ledger**, physical object identification,
liquid/grasp sensing, full task semantics, whole-arm geometry or robot protection
qualification. Base motion and material manipulation remain unsupported in
household mode; the gripper remains a fixed simulator fixture.

## Facts, identity and execution

Only the existing authenticated World role supplies semantic facts. VLA proposals
carry identity/revision references only. `item_id` and region IDs are asserted by
the trusted observer: they must remain stable for one physical/logical generation.
Known material or region kinds cannot change under the same ID. A new ID is a
trusted registration of a different generation, **not proof of physical identity**.
A compromised observer can rename/misidentify objects and remains outside scope.

The authorizer retains the union of **known observed** container contents. An
observation claiming an empty container cannot erase previously observed bleach.
Fresh trusted coverage may resolve temporary sensor uncertainty; unknown input
never grants execution. No trajectory outcome adds/removes material contents.
There is no decontamination, empty-container, pour-success or effect-commit API.
The status/report always records `material_effects_committed: 0`.

Raw signed snapshots and their equality/revision checks stay unchanged. Execution
uses a separate container overlay for both admission and active-arm rechecks.
Current measured bounds are used when a remembered container is observed. If a
registered container is absent, the closed-scene household scope becomes unknown
and refuses motion until it is observed again. Its last bounds are not fresh
geometry authority. This conservative closed-scene requirement can reduce
availability; open-world object retirement is deferred.

## Step lifecycle and recovery

A successful admission reserves the exact emitted trajectory before positive
stdio output. Its digest is SHA-256 of the typed proposal with the approved
`action`, serialized by `serde_json::to_vec`; it includes ID, source, original
time and full semantic binding. This is an **audit digest**, distinct from the M3
wire permit digest, and grants no controller authority.

Steps are consumed within `(task_revision, task_id, step_id)`. A fresh VLA counter
cannot repeat a consumed step. Only a newer **trusted observer** task revision
starts a new task epoch; old entries can then be retired because the durable
world/task floor and exact binding prevent old-epoch admission. This is not
lifetime deduplication of a logical task across observer-authorized new epochs.
The current observer contract requires a newer task revision for a different
task or step ID: in normal operation it admits one motion per task revision.

One unresolved motion blocks further motion. STOP, faults, malformed/rejected
input, stale observations, revocation and restart preserve an `interrupted`
outcome. They do not invent material changes. On reopen, the authorizer requests
cancellation, keeps motion unarmed and requires new measured stop evidence.
Two strictly advancing, fresh World samples must show all four named joints
within configured position limits with `|velocity| <= 0.01 rad/s`, and measured
base twist within 0.01. Source time, semantic coverage and confidence must also
be valid. Resetting operator mode preserves the history.

Ordinary expiry can enter `awaiting_stop`. Only post-expiry fresh stop samples
with the original context, sample time at/after the trajectory end and all joint
positions within the existing tracking tolerance of the final waypoint produce
`motion_settled`. Off-target, changed-context and interrupted outcomes never
become success. A resolved interrupted motion additionally requires a newer
trusted task revision, explicit STOP/rearm and a new approved proposal.
Settling proves **kinematics only**, never a task effect.
An explicit STOP/rearm or stale observation during `awaiting_stop` interrupts
the record; consumers must wait for `motion_pending: false` before rearming.
The signed ROS adapter mints no further positive permits during the last
controller admission reserve of an already admitted arm goal. A timely reply
can cross the deadline: this path still mints nothing, and the expired final
controller holds while Rust's next tick cancels. The request must have begun
before original expiry. Existing permits keep their original expiry, and Rust's expiry/cancel and measured stop checks
remain required. World or round-trip staleness still interrupts immediately.

## Persistence, bounds and clocks

History-bearing state uses `v: 2`; non-household legacy state stays `v: 1`.
The first v1-to-v2 initialization records `history-initialized` and starts with
no historical facts. It does not reconstruct facts from earlier software.
Mode, role epoch/counters, observation floor, contaminants and motion reservation
share one atomic rename/file-and-directory-sync transaction. Positive output
is discarded if that checkpoint fails. A non-cancelling household World reply
also waits for its checkpoint. Urgent STOP/cancellation is emitted first and
then checkpointed: a cancelling reply is **not a durable observation receipt**.
The trusted observer must re-publish the same facts/revision after a cancelling
reply and wait for a non-cancelling World acknowledgement before treating them
as durable. Process close does not emit a Cancel reply; the independent final
controller watchdog must stop it, and restart still requires measured recovery.
Interrupted reservations persisted before execution remain the recovery guard.

Existing/unparseable, oversized, wrong-version, inconsistent or pin-mismatched
protected state fails startup without replacing the state. Offline `state set`
preserves history; it cannot clear a consumed step or change contents. Trusted
recovery requires inspecting/restoring the state. Deleting it is explicit history
loss, outside this guarantee. Downgrading to a pre-v2 binary is unsupported:
older code can overwrite unrecognized state and must not access this file.

Bounds: 256 registered item IDs, 512 region IDs, 64 retained containers, 64 consumed
steps per trusted task epoch, 128 input regions, and a 1 MiB state read/write cap.
New entries at capacity are refused; STOP remains available and already registered
scenes can still operate. A trusted newer task epoch permits bounded step
retirement. Object/scene enrollment changes and capacity reclamation need a
future reviewed provisioning contract; entries are never silently evicted.

The exact robot/model/tool pins must match on restart, including trust-key epoch
rotation. The scene/task floor is durable, including the raw snapshot's original
observation time. A restart restores **no fresh world or motion authority**.
Equal revisions require the same safety facts; lower revision/task/time is
refused. The reference requires the same continuing clock domain and monotonic
observer revisions. Observer/clock restart at zero fails closed; durable observer
clock-epoch transition/rebinding is deferred. No 50/200/250 ms budget is widened.
The reference is not hard realtime; late processing loses authority.

## Evidence

```bash
cargo test -p haetae --test household_enforce_cli
cargo test -p haetae-enforce --test household
./haetae-demo hazards
```

Native authenticated stdio tests exercise reservation-before-output, actual
process kill/restart, operator reset, floor rollback, corruption, atomic-write
failure, retained bleach after an empty claim and a normal non-touching control.
Pure-motion completion and interrupted recovery are separate assertions.

The Gazebo household fixture uses distinct stable logical item/material and
native target IDs, plus fresh step contexts for deliberate new trajectories.
The chemical visual prop represents two **different injected material identities**;
it is not an identity classifier or a fluid-transfer experiment. Its container
remains registered and its native pose is observed even when moved offstage.

The public household report requires the existing six refusals/normal motion
controls, six negative controls and one additional history check (13 total).
The latter requires a settled moving control followed by a fresh-counter
consumed-step rejection, and the actual Rust chemical denial after the raw World
claims empty contents while the authorizer retains a contaminant. Drift must stay
within 0.02 rad. Missing history evidence cannot produce a passing candidate.
