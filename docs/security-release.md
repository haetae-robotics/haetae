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
2. A trusted perception and health path signs world and faults. In the secured
   Gazebo container it has a separate OS identity and role signer; the VLA
   signer owns only VLA authority. The signed-only gateway binds each topic
   to its expected role and supplies trusted receive time. Legacy ROS smoke
   transports still perform source signing within the bridge.
3. The Rust enforcer verifies the signed trust bundle, exact policy hash,
   input signature, role, epoch and increasing counter before judgment. Only
   the ROS bridge publishes `/cmd_vel` and submits arm action goals. The base
   and arm controllers independently stop or cancel on lost bridge input.

The actuation bridge, its OS account, the trusted perception path, the
controller configuration and the SROS2 keystore are in the trusted computing
base. A compromised bridge or privileged host can still command the robot.

## Attack coverage

| Attack | Current code | Evidence required before release |
|---|---|---|
| Compromised model proposes overspeed or forbidden base path | Disc sweep, braking horizon, speed/turn clamp; denials output zero | ROS kinematic scenarios, measured controller deceleration and latency |
| Model continues after a human appears | Every world update and tick rechecks the active velocity; revoke disarms until a zero command | ROS end-to-end latency and minimum-gap measurements |
| Arm chunk exceeds joint bounds or rate | Short joint trajectory validates start, position, velocity, acceleration, final rest and tracking | Joint controller action integration, cancellation and actual controller interpolation |
| Human appears while arm moves | Arm command is cancelled and remains disarmed until two stationary world samples | ROS action cancellation test and independent hardware stop test |
| Forged model, world or fault JSON | Role-bound Ed25519 input signatures; source must match signing role; reference Fast DDS ACL denies a VLA writer | Repeat the SROS2 `Enforce` attack on the selected robot graph with protected key files |
| Replayed signed message | Per-role monotonic counter and persisted epoch/checkpoint | Crash/restart/replay tests across operator reset and key rotation |
| Policy or trust file changed | Root-signed trust bundle binds exact policy bytes | Provisioning and rollback test on target host |
| Gateway dies or its response stalls | ROS bridge publishes zero on failure; base deadman is required | Kill -9 tests for bridge and Rust child against configured controllers |
| Durable checkpoint delays a positive response | Bridge rejects late actuation by response age, proposal expiry and world age; it publishes zero, asks the live Rust child to seal an incident, then exits | Measure worst state fsync on target host; test delayed positive base/arm outputs against controller deadman |
| Rogue direct `/cmd_vel` publisher | Resolved controller-topic graph count raises persistent Hold through the trusted local IPC after discovery | SROS2 permissions must prevent the publisher from connecting |
| Privileged host compromise, key theft, bad perception, unsafe physics | **Not prevented** | Separate host hardening, key protection, sensor validation and certified safety layer |

## Current implementation limits

- The separate household profile enables a root-policy-bound mandatory
  semantic check in Rust for every protected arm client. Trusted signed worlds
  supply fixture facts; Rust derives FK from the exact signed joint plan and
  pinned policy model. It rejects missing/mismatched references, unsafe paths,
  unsupported operations and nonzero base motion in its stationary scope.
  Active motion is rechecked against fresh trusted facts and remaining path.
  This M1 layer has no controller-side authorization proof: a compromised
  gateway retains actuator authority. Whole-arm coverage, persistent effect
  history and physical qualification are not implemented. See
  [household gate contract](household-gate.md).

- The Rust core, subprocess and signed ROS bridge smoke tests run in CI on
  Jazzy. The kinematic base simulator and mock arm action server are software
  models. The Gazebo Harmonic reference also exercises a differential-drive
  controller and a four-joint OpenMANIPULATOR-X trajectory controller on the
  manufacturer ROSbot XL model under physics, including
  base timeout after gate death. The reference arm uses the
  `haetae_arm_guard/LeaseTrajectoryController` plugin: a 250 ms lease checked
  against simulator and monotonic wall clocks holds measured positions and
  discards the old trajectory on gateway loss. Gazebo kill, Rust stall and
  delayed gateway tests run in CI. This does not measure hardware stopping or
  survive failure of the controller process or host.
- The arm check covers joint space. It does not compute 3D link geometry,
  self-collision, singularities, torque, force, payload or contact. Arm motion
  is denied when any human is present in the current world, but absence of a
  human report is not a safety proof.
- Every arm chunk is at most the policy's `max_duration_ms`; the controller
  must cancel on a request and independently stop on gateway death. A generic
  `joint_trajectory_controller` action alone is not proof of this property.
- `ros/security/haetae.policy.xml` has a hosted Jazzy/Fast DDS negative
  test: a VLA enclave cannot create publishers for `/cmd_vel`, world or fault,
  or call the arm action, while authorized traffic succeeds. That result is
  limited to the reference graph. The selected robot graph, its actual
  controller and OS isolation remain untested. Publisher counting detects a
  discovered extra writer; it cannot prevent a command that reaches the
  controller first.
- The Docker Gazebo run has a separate SROS2 enclave for its attacker process.
  That process's direct base command and forged world publisher are denied on
  the actual Gazebo ROS graph while an authorized VLA proposal succeeds.
  The secured container uses separate gateway, world/fault signer and VLA
  signer and proposal-writer UIDs (2001/2002/2003/2004), private role keystores, unpredictable fresh keys
  and role-scoped DDS permissions. Gateway and VLA principals fail to read
  perception signing/DDS keys; gateway cannot create raw/signed world/fault
  writers, and VLA/world cannot create controller or heartbeat writers or arm
  clients. Matched authorized publishers and actual accepted motion are required
  positive controls. The root scenario provisioner and simulator remain trusted;
  The ROS ACL alone does not protect Gazebo Transport. The secured container now also restricts non-root egress to local DDS UDP and launches every role with an inherited network syscall filter. An exact signed-command replay and a modified signed world report are
  also rejected by a fresh production enforcer with zero output. This is
  reference evidence, not a robot-specific security boundary.
- EOF from the bridge now leaves the persistent `running` marker set, so a
  restart enters Hold. The enforcement incident recorder seals each incident
  before continuing. A crash during the append itself can still leave an
  incomplete tail and must be handled as incomplete evidence.
- Legacy ROS smoke/kinematic transports still sign all configured input roles
  in the bridge and do not provide key isolation. `--secure-graph` Gazebo instead
  uses external role signers and a signed-only gateway. Source counters are
  exclusively locked and durably reserved before publishing; source restart
  continues increasing them. The gateway still has actuation and audit authority:
  compromising it can command the controller directly or misuse its heartbeat.
  This stage prevents perception impersonation by that UID, not arbitrary
  malicious actuator behavior. Root, perception, simulator and controller
  compromise remain outside the claim. Native Gazebo injection is blocked only for the sandboxed non-root reference roles; unrestricted host/root processes remain trusted.
- A counter checkpoint is fsynced before any nonzero base or arm execute
  output. Zero and arm cancel output go first so a stop does not wait for
  storage. A crash between a stop output and its checkpoint can still lose
  that stop's counter; the unclean-start Hold requires an offline reset.
  Replay after a completed checkpoint and reset has a CLI regression test.
  The stop-output crash window needs a targeted fault-injection test before
  protective release.
- No hardware or certification evidence exists. Controller limits in policy
  must match measured hardware limits. The system must remain behind the
  robot's independent certified safety functions.

## Release blockers

All of these are required before claiming that Haetae protects a robot from
untrusted or compromised AI commands:

1. The hosted Jazzy/Fast DDS reference runs the eight base scenarios five
   times with logs and traces. Gazebo also exercises a differential controller
   and its timeout. Repeat this on the selected real controller and measure
   its stop distance, latency and minimum human gap.
2. The mock arm action server covers replacement, cancellation, kill and
   tracking faults; Gazebo exercises four-joint action cancellation. A
   configured real arm controller and independent stop path must pass those
   tests with measured latency and position error. A
   robot-specific 3D collision model is required before claiming spatial arm
   protection.
3. The hosted reference graph passes an SROS2 `Enforce` negative test. Repeat
   it on the actual graph: an unauthorized participant must fail to publish
   `/cmd_vel`, world, fault or arm goals. Check effective permissions.
4. Host deployment with a dedicated bridge account, owner-only signing seeds,
   a pinned root public key, immutable policy/trust deployment and documented
   offline reset/key rotation.
5. Independent security review of the immutable release candidate, adversarial
   tests for malformed inputs, signature/key substitution, replay and log/state
   tampering, and a repeat review after any material fix.
6. `SECURITY.md` provides a private reporting channel and supported scope.
   Reproducible builds, signed release artifacts and a claim limited to
   demonstrated robots and attack paths remain to be completed.

No release artifact or registry version should be labelled protective while
one of these blockers is open.

The proposal writer owns only the `/haetae/vla` DDS certificate (UID 2004).
The VLA signer owns a separate `/haetae/vla_signer` certificate and signing
seed (UID 2003). Only that signer can publish `/haetae_gate/signed/vla`;
ROS node names within an enclave are not an authentication boundary.

## Stage 3 simulator perception evidence

The Gazebo reference now contains native person geometry and a real simulated
GPU lidar. Scenario paths move that geometry; only measured occupancy populates
signed worlds. A known calibration return, complete scan validation, original
source age and receipt age guard the controlled bay. Invalid/unknown scans do
not refresh the world; the existing 200 ms world-age stop remains unchanged.
Moving-base tests cover receiver disconnect and removal of the native
calibration target, with no automatic motion rearm after sensor recovery.

This is conservative obstacle occupancy at one adult torso-height scan plane,
not a validated human classifier. Root/simulator, unrestricted host processes, calibrated
scene assumptions and the trusted perception adapter remain trusted. Physical
sensor failure rates, child detection, occlusions, content poisoning and real
robot stopping/certification blockers above are still open.

## Native transport isolation evidence

`--secure-graph` requires Docker `--cap-add=NET_ADMIN`, `iptables`, `iproute2`
and `python3-seccomp`. Failure to install the IPv4/IPv6 rules or role syscall
filter aborts the run. Root installs container-local OUTPUT rules before roles
start. Non-root roles may send IPv4 UDP to loopback, this container's IPv4
addresses and the DDS discovery group, on this ROS domain's RTPS ports only.
No general TCP/UNIX/IPv6/packet socket or io_uring network route is available
to these roles; internal UNIX socketpairs remain available. Dropped capabilities
and `no_new_privs` prevent a role exec from acquiring root file capabilities.
This boundary assumes root launches roles without inherited network descriptors.
It is not a general OS sandbox or a resource exhaustion defense.

`transport-isolation.json` records all four role UIDs and the restricted external
attacker UID 65534. Each must retain allowed UDP delivery, fail TCP/UNIX/IPv6/packet
socket creation, retain restrictions after exec, fail delivery to a root UDP
receiver outside DDS ports, and fail an actual Gazebo pose request. The same
request by root must alter the real lidar calibration return before restoration;
root delivery to the forbidden receiver must succeed. Existing DDS matched-writer
and approved robot motion controls must also pass. This covers these container
principals, not a remote Gazebo deployment, root, host, trusted perception or a
compromised gateway's legitimate ROS actuation authority.

A timely positive response that crosses the original proposal/world expiry is
discarded before output. The bridge sends zero and requests arm cancellation
first, then explicitly rejects the engine's active goal and armed sources. A
zero, non-executing, disarmed response is mandatory. Sensor recovery alone
cannot resume motion. The sensor oracle accepts either engine `stale_world` or
a disarmed rejection with the exact response-boundary world-expiry error.
Malformed responses, backwards clock, IPC failures and responses taking 50 ms
or more still trigger the fatal stop path. No freshness budget is increased.

### Defensive boundary corrections

The authenticated stdio adapter accepts signed source input plus local `tick`,
`reject`, and upward-only `hold` (`{"k":"hold","t":...}`). Only the trusted
local gateway owns that pipe. `hold` records a gateway authority fault; signed
stop/move input cannot lower the resulting mode. It confers no world or fault
signing key on the gateway.

Nonzero base velocity requires measured linear and angular twist. Joint
trajectories require Normal mode and joint-policy limits; Caution cancels them.
ROS goal acceptance and cancellation each have a 250 ms absolute response
limit. Cancellation/engine settling suppresses lease renewal, and lease renewal
follows successful output handoff.

Privileged simulator evidence reads walk from a trusted run root without
following symlinks, require a regular single-link file and the expected role
owner, and reject files above 256 MiB. Public checkpoints are atomic and 0644;
private provisioning remains 0600 beneath root-owned 0700 anchors until handoff.
These are simulator boundary checks, not evidence of physical stop performance.
