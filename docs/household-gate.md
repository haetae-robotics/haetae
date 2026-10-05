# Mandatory household execution gate: M0/M1 contract

Status: simulator evaluation, non-safety-rated. This increment makes the
household checks mandatory in the central Rust execution path when a trusted
policy contains `household`. It does not qualify a robot for physical protection.
The milestone list below separates the later controller, state-history,
geometry and hardware work.

## Protected scope

The reference target is ROSbot XL with OpenMANIPULATOR-X, four joints in order
`joint1`–`joint4`, a stationary base and a fixed household fixture tool. Only
short position-only `joint_trajectory` chunks are supported in household mode.
Nonzero base commands and move/grasp/place operations are refused. STOP and
zero base commands remain available without a semantic snapshot.

The gate derives a swept end-effector/item sphere from the exact proposed
joint waypoints, trusted measured joints/base and policy-owned FK transforms.
It checks human and fall volumes, heat/material, electricity/material,
water/electrical-item and the explicit bleach/ammonia container combination.
The path is never supplied as an authoritative model claim.

The sphere is conservative within this lab's pinned tracking/interpolation
bounds. It does not cover every arm link, tool orientation, base stability,
grasp success, real liquid transfer, unknown chemistry, throwing or dropping.
The gripper is held by a trusted simulator controller. A completed trajectory
does not prove a physical transfer or establish general task safety.

## Execution authority inventory

This inventory describes the isolated reference container. Host/root, simulator,
trusted observer, provisioning and the ROS gateway remain trusted. The SROS2
policy is [gazebo.policy.xml](../ros/gazebo/gazebo.policy.xml); controller setup
is [controllers.yaml](../ros/gazebo/controllers.yaml).

| Input or execution path | Authority and current protection |
| --- | --- |
| `/vla/arm` and `/vla/cmd_vel` | The proposal role can publish these inputs. A separate VLA signer signs typed proposals. Every signed non-stop proposal must pass the Rust gate; nonzero base motion is refused in household mode. |
| `/haetae_gate/signed/vla` | VLA signer only. A valid signature authenticates an untrusted proposal; it does not approve the action. Missing/changed semantic references still fail. |
| `/haetae_input/world`, `/haetae_gate/signed/world` | Trusted perception/observer and separate World signer. Household facts enter only inside the typed signed world snapshot. The household lab injects item/device/content facts as test fixtures. |
| `/haetae_input/fault`, `/haetae_gate/signed/fault` | Separate Fault role key. Faults can raise incident mode. The VLA key cannot authenticate this role or lower the mode. |
| Rust `enforce --stdio` | Authenticates roles and root-bound policy, checks input/replay counters and mandatory household conditions, then emits zero/cancel or the exact approved trajectory. A durable counter checkpoint precedes positive output. Its transport clock is supplied by the trusted gateway. |
| `/diff_drive_base_controller/cmd_vel` | Gateway publication authority; VLA roles cannot write directly. Central household policy forbids positive base output. The controller's independent 250 ms command timeout remains; it does not verify a Rust action permit. |
| `/joint_trajectory_controller/follow_joint_trajectory` | Gateway may call this action. The controller checks goal freshness and its independent lease, while the gateway executes Rust output. It does not yet authenticate a permit for the exact trajectory. |
| `/joint_trajectory_controller/joint_trajectory` | Controller topic ingress also checks freshness/lease. Untrusted VLA/signer enclaves lack direct publication authority. Trusted simulator/root can change the graph and remains outside the protected attacker scope. |
| `/haetae_gate/heartbeat` | Gateway publication authority. The controller checks freshness using ROS and steady clocks. The heartbeat is not a cryptographic, action-bound semantic approval. |
| `/gripper_hold_controller/joint_trajectory` and its action | Trusted simulator fixture holds the gripper. No model grasp path is exposed. This separate actuator is not protected by household semantic execution permits; actual manipulation remains disabled. |
| `/controller_manager/*`, controller parameters/configuration | Trusted simulator/root authority provisions and switches controllers. VLA/signer roles lack manager authority. Gateway compromise and privileged graph reconfiguration are not covered by M1. |
| Gazebo Transport `/world/empty/set_pose_vector`, `/world/empty/pose/info` | Trusted root lab/perception may place and observe fixtures. SROS2 does not secure Gazebo Transport. Separate UIDs, the container network guard and role sandbox restrict nonroot roles to the allowed local DDS network; host/root remain trusted. |
| UNO USB/GPIO | H1 is a trusted-USB LED bench. Separate [H2 permits](controller-permits.md) authenticate an OFF-only connection and every exact LED action in the MCU. Neither is motor/household ingress; final ROS arm/base permits remain M3 work. |
| Web controls | Start/advance the root-owned fixed lab scenarios. They do not select policy bytes, create signed observer facts or submit arbitrary model motion to the protected gate. |

The relevant isolation implementation is
[role_isolation.py](../ros/gazebo/role_isolation.py),
[network_guard.py](../ros/gazebo/network_guard.py) and
[role_sandbox.py](../ros/gazebo/role_sandbox.py). These are container boundaries,
not a general host sandbox. Deployment must preserve them and the trusted
gateway boundary. A compromised gateway still has controller command and
heartbeat authority: defending that case requires M3.

## Policy and startup requirements

`Policy.household` is optional for legacy policies. Absence means the legacy
protection scope; it must never be reported as mandatory household protection.
Presence activates the mandatory checks for every non-stop action, independent
of the proposal's source or whether a caller invoked `hazard-judge` first.
There is no model/web/CLI override that disables these checks for the loaded
household policy.

Household `enforce --stdio` startup requires all of:

- `--trust` containing a root-signed trust bundle;
- `--root-pubkey` pinned by trusted provisioning;
- `--state` for durable incident/replay state; `--ephemeral` is refused;
- `--sillok` and `--key` for recording; missing recorder is refused.

The trust bundle commits to SHA-256 of the **exact policy file bytes**, an
audience, epoch and distinct World/Fault/source public keys. Changing any policy
byte without a new root-signed bundle fails startup. Files, root selection,
epochs and private credentials are protected by deployment permissions; the
signature does not defend against an attacker controlling host/root or all
trusted provisioning.

All household policy fields below are required. Unknown fields are refused.
IDs use 1–64 ASCII bytes from `[A-Za-z0-9_.-]`; SHA-256 values use exactly 64
lowercase hex characters. No implicit geometry/material defaults are applied.

| Field | Source, unit and limit |
| --- | --- |
| `schema_version` | Root policy; exactly `1`. |
| `robot_id`, `tool_id` | Root policy IDs; the lab uses `rosbot_xl_open_manipulator_x` and `household_fixture_v1`. |
| `model_sha256` | Root policy digest of the pinned model representation used to derive FK. Rust verifies binding agreement; it does not download a model or certify the supplied transforms. |
| `chain` | 1–32 ordered URDF origin transforms. Each `xyz` and `rpy` has three finite numbers. Translation is metres, each component within ±1; rotation is radians, each component within ±2π. Origin rotation is `Rz(yaw) Ry(pitch) Rx(roll)`, followed by the optional local joint rotation. |
| `chain[].joint_index`, `axis` | Both absent for a fixed transform, or both present for one joint. Every index 0–3 occurs exactly once in order. Axes are `y` or `z`. Total link translation length ≤1 m; summed downstream lever lengths ≤1.4 m. |
| `sample_step_rad` | Exactly `0.002` radians. FK samples connect every segment of the submitted joint plan; derived path ≤4096 points or the proposal fails. |
| `swept_radius_m` | Exactly `0.15` m, including the lab attachment/tracking/interpolation margin. No proposal radius override. |

Household policy additionally requires an arm policy with four named joints.
Joint positions are radians, velocities rad/s and accelerations rad/s².
Each joint's configured position range must be inside the following bounds:
`joint1` [−0.8π, π], `joint2` [−π/2, π/2], `joint3` [−1.5, 1.4],
`joint4` [−1.7, 1.97]. Velocity ≤1 and acceleration ≤40; configuration may be
stricter. Start error ≤0.01 rad, tracking error ≤0.05 rad and minimum confidence
≥0.9. General arm validation also requires 3–256 points, positive duration and
finite ordered limits. Household duration is ≤1000 ms and no greater than the
proposal freshness budget. World freshness must be ≤200 ms; the generic
500 ms world default is therefore not accepted for a household policy.

## Trusted world contract

`WorldSnapshot.semantic` may be absent on an input, but its absence prevents
household motion. Presence requires all fields listed below and rejects
unknown fields. The outer world still requires measured robot pose, yaw,
twist and all four joint samples in policy order for protected motion.

| Field | Source, unit and limit |
| --- | --- |
| `schema_version` | Trusted observer; exactly `1`. |
| `revision`, `task_revision` | Positive `u64` observer revisions. Safety facts change only with a newer scene revision. Revisions are context bindings, not a durable task/effect ledger. |
| `task_id`, `step_id`, `robot_id`, `tool_id`, `item_id` | Trusted observer IDs under the same 64-byte syntax. Robot/tool must match root policy. Stable physical identity and effect provenance require later M2 work. |
| `model_sha256` | Observer model identity; exact root-policy digest match. |
| `observed_ms` | Original trusted observation time in the same runtime clock domain as outer `stamp_ms` and stdio `t`. Re-signing or publishing a new world does not renew this original stamp. |
| `confidence` | Finite [0,1]; executing requires ≥0.9. A signature authenticates the observer, not the confidence claim's empirical calibration. |
| `coverage_known` | Explicit boolean; false refuses execution. |
| `item` | `knife`, `pressurized`, `flammable`, `conductive`, `battery`, `electrical`, `bleach`, `ammonia`, `inert` or `unknown`. Unknown refuses execution; no material class is inferred from the model proposal. |
| `regions` | At most 128 entries with unique valid IDs. See the region contract below. |
| `base_pose` | Trusted scene anchor `{x,y}` in world-frame metres, finite components within ±100 m. |
| `base_yaw` | Trusted scene anchor heading in radians, finite and within ±2π. |

A region requires `id`, `kind`, `state`, `bounds`, `contents` and
`contents_known`. Kinds are `human`, `heat`, `electrical`, `water`, `container`,
`fall`, `surface`, `unknown`; states are `active`, `inactive`, `unknown`.
`bounds.min/max` each contain finite world-frame `{x,y,z}` in metres within
±100 m, with min ≤ max on every axis. Contents contain at most 32 item kinds.
Unknown kinds/states/materials refuse execution. A touched container with
`contents_known=false` is refused. Human/fall volumes are excluded regardless
of an inactive label. Heat/electrical inactive state permits the corresponding
device rule only when other checks also pass. The material relation table is
the explicit set in [hazard.rs](../crates/haetae-core/src/hazard.rs).

For admission, neither the outer stamp nor original observation may be future
dated or older than 200 ms; original observation cannot postdate the outer
stamp. Base measured linear/angular speed must each be below 0.01 m/s or
rad/s, and position/heading must stay within 0.002 m/rad of the scene anchor.
New observations are validated for monotonic scene/task revisions and original
time. Republishing unchanged revision with changed safety facts is rejected.

The clock is a deployment trust boundary. The reference gateway uses ROS
simulation time for `t`, `stamp_ms`, `observed_ms` and proposal timestamps.
The stdio harness's `t` must not be taken from an untrusted proposal. Transport
time regression fails closed. Observation freshness, proposal freshness and
the controller's independent dual-clock lease are distinct checks; monotonic
host time or device time must not be directly compared with ROS timestamps.

## Untrusted proposal contract

`ActionProposal.semantic` contains only references:
`schema_version`, `world_revision`, `task_revision`, `task_id`, `step_id`,
`robot_id`, `model_sha256`, `tool_id`, `item_id`. For admission, every field
must match the trusted snapshot and root policy; `world_revision` must match
the snapshot's `revision`. Facts such as item kind, container contents,
confidence, device state, Cartesian path or a claimed approval are not allowed
inside this binding. The VLA/source signature covers the exact payload including
these references and the joint command. Changing the source still requires the
matching role key and permitted source policy.

The ROS arm source carries these untrusted references in `header.frame_id`
as `haetae.semantic.v1:` followed by JSON. This adapter representation has a
2048-character cap, rejects duplicate/extra fields and requires schema `1`.
Legacy frame names provide no references and therefore fail protected motion
at the Rust gate. The adapter uses the same 64-byte ASCII ID syntax as Rust. All stdio input lines and signed inputs are bounded
at 1 MiB. Authenticated mode refuses unsigned world/proposal messages.

Position-only waypoints have `time_from_start_ms` in milliseconds and exactly
four joint positions in radians. The first time is zero, following times are
strictly increasing, final time is no later than `ttl_ms`, the start matches
measured joints and the final segment has zero commanded speed. Joint limits,
velocity and acceleration checks apply in addition to household FK checks.
The isolated lab signer uses a fixed ≤1000 ms lease; a malformed arm source
input becomes a signed STOP instead of a partially interpreted motion.

When an approved chunk is active, the enforcer retains its admission context
and rechecks the remaining path against new trusted observations. Missing,
stale, unknown or mismatched context cancels and disarms the action. A newer
world revision cannot silently exchange its robot, task/step, tool or item
identity. A newly introduced hazard cancels the current action. Tracking,
freshness, incident mode and independent controller timeout checks remain.

## Failure and recovery

Invalid signatures, policy/hash mismatch, missing context, stale original
observation, unsupported actions, mismatched references and hazardous paths
never produce `ArmOutput::Execute` or positive base commands. If an action was
active, the response includes cancellation and the existing settling lock.
STOP/cancel publication does not wait for the durable checkpoint; positive
execution does. State/recorder failures prevent positive execution.

Accepted role counters persist before actuator-facing output. An unclean
process/transport restart enters Hold; an offline operator reset does not erase
counter history. Recovery requires fresh trusted observations and explicit
source re-arm. There is no automatic motion restart after a denial or failure.
The ROS transport must publish zero/cancel and the final controller must stop
independently when the process dies; emitting a zero Step alone is not physical
stop verification.

## Verification and claim limits

[household_enforce_cli.rs](../crates/haetae/tests/household_enforce_cli.rs) runs
the real `haetae enforce --stdio` binary with a root-signed exact-byte policy,
distinct World/Fault/VLA keys, persisted state and a recorder. Its synthetic FK
chain and fixed seeds are explicitly test fixtures. It checks exact safe
trajectory output, missing/mismatched/stale references, a changed hazardous
trajectory with identical references, nonzero base refusal, role/context
forgery, policy-byte tampering, STOP without context and restart replay refusal.
Core/enforcer tests cover decision and active-command behavior. The Gazebo lab
provides measured motion controls; CLI output alone does not show a robot moved.

Run the signed boundary tests with:

```sh
cargo test -p haetae --test household_enforce_cli
```

Current M1 claim: the untrusted source cannot skip household checks on motion
accepted by the central protected Rust gate. Signed observations authenticate
source and integrity, not physical truth. In the household lab, materials,
device states and container contents remain injected fixtures.

The following remain unimplemented protection milestones:

- **M2:** stable object identity, verified physical effects, a durable content/
  uncertainty ledger and restart-safe task consumption history.
- **M3:** an isolated approval identity and an authenticated exact-action permit
  verified by the final arm/base controller; defense against a compromised ROS
  gateway or its heartbeat authority.
- **M4:** full arm/tool geometry and measured stop envelopes under dynamic
  conditions and load; this increment only retains the bounded lab sphere.
- **M5:** whole-task preconditions/effects, harmful sequences and verified safe
  completion beyond the explicit current fixture checks.
- **M6:** selected real robot/controller integration, electrical timing,
  payload-dependent stopping and reproducible robot-specific qualification.

No general household safety, hardware safety certification, arbitrary robot
compatibility or compromised-bridge protection is claimed by this increment.
