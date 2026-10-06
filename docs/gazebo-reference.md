# Gazebo ROSbot XL reference

This reference connects the Rust enforcer and ROS bridge to a Gazebo Harmonic
mobile manipulator through `gz_ros2_control`. Its differential-drive controller
receives Haetae's `/cmd_vel`; its joint trajectory controller receives Haetae's
`FollowJointTrajectory` action. Gazebo joint states and odometry feed the world
publisher. The default product model is **Husarion ROSbot XL with ROBOTIS
OpenMANIPULATOR-X**, using standard rubber wheels and four monitored arm joints.
Haetae has no 3D arm collision policy.
The Rust owner signs exact commands onto `/haetae_authorized/cmd_vel` and a
separate untrusted relay forwards them to `/diff_drive_base_controller/cmd_vel`.
The wheel and arm controllers verify the pinned signatures themselves. See
[controller permit contract](gazebo-controller-permits.md).

The test checks approved base motion, a human-triggered zero command and base
stop, out-of-bounds arm denial, arm action cancellation, a sealed incident log,
and the base controller's command timeout after the bridge is killed. The
person geometry is native Gazebo geometry. A GPU lidar measures its surface;
the controlled-bay occupancy adapter conservatively treats obstacles as people.
This is not a camera classifier or a real perception sensor. The
reference arm uses `haetae_arm_guard/LeaseTrajectoryController`, a position-only
ros2_control plugin with exact-action permits and an independent, at most 200 ms
authorization lease. It checks both
simulator time and monotonic wall time, rejects replayed, delayed and far-future
heartbeats, holds measured joint positions on expiry, and discards the old
trajectory. Heartbeat recovery alone cannot resume it. This guard still depends
on the controller, simulator and OS running; it is not a hardware safety stop.

After the base timeout test, the live scene shows three moving-arm faults:
bridge kill, Rust child stall, and delayed bridge traffic (SIGSTOP/SIGCONT).
Each uses a separate fresh test fixture after measured base stop; these fixtures
are not an automatic production restart or operator reset. The controller's
transition time, observation delay, all four held positions and post-stop drift
are recorded in `result.json` under `arm_faults`. A dedicated headless case is
available with `--arm-fault kill`, `stall` or `delay`.

## Run headlessly on Ubuntu 24.04

Use ROS 2 Jazzy and Gazebo Harmonic. The official supported pairing is
documented by [Gazebo](https://gazebosim.org/docs/harmonic/ros2_integration/)
and [gz_ros2_control](https://control.ros.org/jazzy/doc/gz_ros2_control/doc/index.html).
The [CI job](../.github/workflows/ci.yml) uses the same package set:

```bash
sudo apt-get update
sudo apt-get install -y python3-cryptography ros-jazzy-rmw-fastrtps-cpp xvfb libgl1-mesa-dri \
  ros-jazzy-ros-gz ros-jazzy-gz-ros2-control ros-jazzy-ros2-controllers \
  libssl-dev ros-jazzy-controller-manager ros-jazzy-robot-state-publisher ros-jazzy-xacro
source /opt/ros/jazzy/setup.bash
colcon --log-base /tmp/guard-log build --base-paths ros/haetae_arm_guard ros/haetae_scene \
  --merge-install --build-base /tmp/guard-build --install-base /tmp/haetae-guard
source /tmp/haetae-guard/setup.bash
cargo build --release --locked -p haetae
export ROS_DOMAIN_ID=81 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
python3 ros/gazebo/run_reference.py target/release/haetae --out /tmp/haetae-gazebo-run
cat /tmp/haetae-gazebo-run/result.json
```

## Watch the running simulation

### macOS or Linux with Docker

With Docker Desktop running on macOS, or Docker Engine running on Linux, run
from the repository root:

```bash
./haetae-demo start
```

The first run builds a ROS 2 Jazzy and Gazebo Harmonic image and a Linux
Haetae binary for the host's CPU architecture. Open
`http://127.0.0.1:8765/` after the terminal prints the readiness message. Wait until
the robot appears, then press **시뮬레이션 시작** in the bottom transport. The
runner sends its first movement command only after that click. The large scene
shows the robot; the panel next to it explains each step in plain language.
The Docker viewer runs one scene at a time. After each measured stop or
completed check, it holds the scene until you press **다음 단계** in the same
bottom transport. Native people remain present while you inspect a
stop; advancing makes the person depart before the next scene. Marker
appearance fades in the reconstruction, but safety inputs and stop commands
are never delayed. Normal base movement lasts at least three seconds in the
live view; headless checks keep their original timing. Physics and monitoring
continue between scenes. Each wait, and the final result view, lasts up to one
hour. Use `./haetae-demo restart` after a session expires. `--step-through` enables this behavior when launching the runner directly.
The default **라이브 3D** tab rebuilds the robot from Gazebo odometry and
joint telemetry and the observed native person torso pose. Teal points show
actual lidar obstacle surface measurements. It is explicitly labeled
as a reconstruction. **Gazebo 원본** opens the actual Gazebo GUI relayed
from the container through a local noVNC connection.
The studio uses a compact graphite interface, a light test bay, and a persistent
start/next control. The robot uses the manufacturer body, wheel and arm geometry,
inertia, joint axes, physical limits and mounting pose. Sources are pinned in
[`ros/gazebo/vendor/`](../ros/gazebo/vendor/README.md) with Apache-2.0 licenses,
commit IDs and file hashes. The [manufacturer Jazzy repository](https://github.com/husarion/rosbot_ros/tree/jazzy)
supports this mobile manipulator configuration, making it a practical match
for this reference's ROS 2 Jazzy / Gazebo Harmonic environment.

The simulation adapter is `ros/gazebo/rosbot_xl.urdf.xacro`. It starts in the
manufacturer Home arm pose, adds Haetae-named controllers and a 250 ms base
command timeout, and holds the gripper closed. Optional camera/lidar, firmware
control, MoveIt and navigation are not launched; grasping is not tested. The
simulator position gain is 0.5 at 100 Hz, following the
[gz_ros2_control position-interface configuration](https://control.ros.org/jazzy/doc/gz_ros2_control/doc/index.html).
This tunes a simulated servo; it does not model a measured Dynamixel response.
The gate keeps its 0.05 rad tracking tolerance and one-second trajectory limit.

`tools/build_product_visuals.py` converts the same expanded URDF and DAE/STL
meshes into `sim/assets/rosbot-xl.json` and a gzip copy. The browser builds the
real link hierarchy and follows measured positions for every joint, including
wheel rotations. It preserves the source mesh coordinates and materials.
Docker rebuilds the asset automatically. To regenerate it on a ROS host:

```bash
source /opt/ros/jazzy/setup.bash
python3 tools/build_product_visuals.py
```

The native Gazebo scene uses `ros/gazebo/studio.sdf`; its ground collision and
physics settings match the previous empty world. Bay marks are visual only. The live run moves farther than the headless reference check so the
movement is easier to see.
The person is assembled from adult-sized native Gazebo visual/collision parts.
Its torso pose from `/world/empty/pose/info` anchors the browser illustration;
both views show the same world position. The native gait and browser gait use
planted-foot kinematics driven by Gazebo time. The browser skeleton is an
illustration, not a measured human skeleton. Reduced motion suppresses its gait;
delayed telemetry stops striding without extrapolating the root pose.

In the live run, the person enters 0.40 m forward and 1.90 m to the robot's left
and walks toward a point 0.40 m forward and 0.78 m left at 0.24 m/s on the
Gazebo clock. These are world paths anchored to the robot pose at entry, not
markers attached to the moving robot. The path moves native geometry only;
Haetae receives independent lidar surface measurements, not that path. The base continues receiving
movement proposals while the person is far away, then the existing swept-path
proximity rule stops it (0.65 m plus the 0.25 m base footprint). The person
pauses beside the robot as soon as the zero command is observed and stays
there until **다음 단계**. After that click, the person walks away at 0.24 m/s;
the native person is parked off-scene only at the far endpoint. The headless
reference places the native person near the robot, then waits for actual lidar
detection for CI speed.
The transition requires a person verdict in the engine log and a subsequent
observed zero command. A world-triggered revocation does not necessarily emit
a proposal Decision outcome; the scene must not wait for that separate event.

The arm policy remains stricter: any human report cancels the arm, even while
the person is still far away. The arm stops first, the person continues walking
toward the stopped robot, then the scene waits for the viewer. Neither the
report nor the cancellation is delayed for presentation. Walking clearance
includes the 0.29 m silhouette radius and the product arm reach. The result
records `person_entry_to_stop_report_sim_ms` and the distance in the report
that triggered denial. Stop latency is measured from that exact world sample,
matched against the enforcer log, rather than from the first far-away report.
Use the tabs to compare robot motion with Haetae's decisions.
The original GUI may render slowly with software OpenGL, especially in Docker
Desktop. The browser reconstruction remains available in the same session.
The script writes `result.json` and diagnostic logs to
`artifacts/simulator-alpha/<run>/`; set `HAETAE_DEMO_OUT` to change the output base directory. Each run uses a fresh subfolder. Use `./haetae-demo restart` to repeat the scenario.
If the runner fails, the live page shows **시뮬레이션 중단**, disables progress
controls and retains that error after a refresh for the configured live hold
period. Diagnostics include `error.json`. A disconnected stream is labelled
separately from a connected stream whose measurements are delayed.

The Docker run uses SROS2 `Enforce` on the **Gazebo ROS graph**. Gazebo,
its controllers, the trusted world publisher and Haetae get distinct trusted
enclaves. An attacker process with only VLA enclave credentials attempts to
publish directly to the Gazebo base controller and to the trusted world topic.
The run requires an authorized VLA proposal to reach Haetae, then checks that
neither unauthorized message reached the receiving topic and the robot stayed
still. The attacker runs as an unprivileged OS user with a keystore containing
only its VLA enclave; the trusted processes use a separate owner-only
keystore. Fast DDS uses UDPv4 here so those different users can exchange DDS
traffic without relying on shared-memory permissions. The secured Linux container runs the Rust owner/permit authorizer as UID 2001, the world/fault
signer as UID 2002, and the VLA signer as UID 2003 and the proposal writer as UID 2004. Each has only
its private role keystore and permitted signing keys. The authorizer has audit
and permit keys but no source signing keys, and accepts role-bound signed topic inputs.
The separate relay is UID 2005; it has no signing keys or controller-manager
authority and only forwards public signed packets and action traffic.
Fresh unpredictable per-run keys replace the legacy demo seeds. Source counters
are durably reserved before publication and exclusively locked across restart.
The scenario provisioner and physics process remain trusted root processes.
`principal-isolation.json` records failed OS reads of perception credentials;
`role-permissions.json` records denied DDS writers/action clients and matched
authorized writers. The Rust owner/authorizer remains trusted; the compromised
relay cannot authorize different motion or extend authority by restamping.
`controller-permits.json` records the separate final-controller attack fixtures. Gazebo Transport is outside the ROS ACL; the secured container adds a kernel network boundary for non-root roles. See the
[Fast DDS transport options](https://fast-dds.docs.eprosima.com/en/2.14.x/fastdds/env_vars/env_vars.html#fastdds-builtin-transports).

The page also shows an exact signed-command replay and a changed signed world
report against a fresh production Rust enforcer. The first signed moving
command must succeed; the replay and changed world report must return zero.
`attack-result.json` and the `attack_probes` field in `result.json` record each
outcome and its scope. The replay probe is separate from the Gazebo ROS graph:
the ROS bridge converts each new VLA topic delivery into a newly signed input.

Docker publishes ports 8765 (telemetry) and 6080 (Gazebo GUI via noVNC) on
the host's `127.0.0.1` only. Both container services listen on `0.0.0.0` so
Docker can forward those local ports. The VNC server has no password and is
reachable only through this local binding. Do not publish these test ports on
a public interface.

### Native Ubuntu 24.04

On an Ubuntu 24.04 machine with the packages above installed, start the
reference with its loopback-only live page:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=81 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
python3 ros/gazebo/run_reference.py target/release/haetae \
  --live-port 8765 --wait-for-viewer --live-hold-seconds 20 \
  --out /tmp/haetae-gazebo-live
```

Then open `http://127.0.0.1:8765/` on that machine. The runner waits for the
browser before sending the first moving command. The page updates from the
running Gazebo odometry and joint feedback, ROS base commands, and Haetae
decisions. The **라이브 3D** view is a reconstruction; the original GUI tab is
available only when the GUI relay is started. The
reference runs its fixed test once, then keeps the finished page connected for
20 seconds. Restart the command for another run. The HTTP server binds only to
`127.0.0.1`; from another computer, use an SSH port forward rather than
exposing the test endpoint directly.

On an Ubuntu desktop, `gz sim -g` in a second terminal can attach the native
Gazebo 3D GUI to the same server. The separate GUI mode is described in the
[Gazebo documentation](https://gazebosim.org/docs/harmonic/gui/). The live
browser view provides the ROS decisions and measurements that the native GUI
does not label.

The runner uses a fresh temporary state and log directory, backed by `/dev/shm`
when available, so hosted disk scheduling does not dominate the gate's 50 ms
actuation budget. It copies the result and diagnostic logs to `--out`, leaving
test signing seeds out of the artifact. The runner keeps Gazebo and setup logs
next to `result.json`. It verifies `sealed-snapshot.jsonl`, a completed sealed
prefix that contains the arm cancellation; the continuously written live log
may end with an unsealed world update. For a visual
inspection, the generated `reference_bot.urdf` can be spawned in the Gazebo GUI
with the same `controllers.yaml`, but the CI runner intentionally uses the
headless server to keep its evidence reproducible.
The result labels wall time and Gazebo simulation time separately. A hosted
runner may simulate more slowly than real time, so wall-clock stop latency in
this test is not a physical robot's stop deadline.
The [browser replay](../sim/gazebo-replay.html) uses a sealed snapshot from a
successful CI run to show Gazebo odometry and joint feedback alongside the ROS
gate's decisions. It is a recorded data visualization, not a Gazebo GUI video
or a live simulator session. The [evidence note](../sim/evidence/README.md)
identifies the exact source run and verification key.
The live viewer serves [its own page](../sim/gazebo-live.html) from the running
reference process and streams only selected telemetry and verdict fields.

## Boundaries

The world publisher trusts Gazebo odometry and joint states, adds the model's
(5, 5) spawn offset to odometry, and uses actual Gazebo lidar ranges for
occupancy. There is no physical perception sensor, noise model, adversarial
real-world perception validation, SROS2
permission test on physical ROSbot XL hardware, or remote/host Gazebo
Transport isolation. The secured container isolates its non-root roles only. The browser demo and Python kinematic fixture remain quicker
ways to explore policy behavior; this reference exercises actual ROS
controllers and Gazebo physics. Only physical hardware can establish a real
motor's stop time and distance or a real arm's cancel behavior.

## Isolated role implementation

`--secure-graph` requires the root-run Linux test container with `--cap-add=NET_ADMIN`, `iptables`, `iproute2` and `python3-seccomp`; it is not a native
host account installer. A trusted test provisioner creates private principal
folders and launches each child with its UID/GID and no supplementary groups.
`source_node.py` signs world/fault or VLA exclusively. The VLA signer validates
raw proposals; malformed commands become signed stops. The signed-only gateway
rejects envelope roles that do not match the topic binding before passing the
exact signed bytes to Rust. The root test driver has a private stdin pipe to a
VLA-only proposal writer, rather than using perception DDS authority to issue
AI commands. Legacy headless smoke scenarios remain explicitly non-isolated.

The proposal writer owns only the `/haetae/vla` DDS certificate (UID 2004).
The VLA signer owns a separate `/haetae/vla_signer` certificate and signing
seed (UID 2003). Only that signer can publish `/haetae_gate/signed/vla`;
ROS node names within an enclave are not an authentication boundary.

## Native sensor boundary

The world includes a fixed 360-degree GPU lidar at `(3, 5, 1.16)` with 720 rays,
30 Hz target update rate and 0.1–10 m range. The calibrated bay is `[1,10]` in
both horizontal axes; the robot must remain 1.5 m inside its edges, covering
its footprint, proximity rule and maximum configured braking horizon. A fixed
cylinder at `(3,7)` proves the scan renderer sees a known calibration target.
All non-calibration returns in the bay become conservative person obstacles;
this single torso-height plane is **not** semantic human recognition. It cannot
establish safety for children below the scan plane, crawling, complex occlusion,
reflectivity failures or unvalidated clutter. The root simulator, geometry, unrestricted host transport processes and the
calibrated empty-bay assumption remain trusted. Use a validated perception stack
and appropriate sensors before adapting this reference to a real robot.

The adapter checks the complete range vector, configured pose/angles/range,
original simulator stamp, receipt age and calibration return. NaN, malformed,
missing, out-of-order, future, stale (200 ms) or uncovered measurements cannot
become healthy observations. A bounded 16-frame history selects the newest
monotonically accepted scan whose original stamp is no later than the ROS
clock, including an invalid scan. Original source and wall receive ages must
remain below 200 ms; future-only, expired or evicted coverage stays unknown.
Healthy fusion uses the oldest lidar/joint/odom stamp; it never stamps old
sensor data as new. Unknown coverage is delivered as a confidence-zero world
when mechanical observations are fresh. The root-signed `perception-unknown`
rule rejects and revokes motion below 0.9 confidence. The coverage test requires
the accepted unknown world, named Rust revocation, disarm and a subsequent
measured zero command within 400 ms of the actual fault request. Mechanical
observation or signing disappearance retains the existing 200 ms world-age
stop. Sensor recovery cannot rearm a stopped source without an explicit zero
command; a latched household failure suppresses all world publication.

The test removes the **native calibration geometry** to exercise lost coverage,
and disconnects the lidar receiver to exercise stale input while the robot is
moving. `sensor-faults.json` records unknown status, measured zero latency and
non-rearm after recovery. Native person approach, departure, surface-to-torso
agreement and no invented safe world are checked separately from the test path.
Teal points in the live page are measured surface positions, not generated rays.
The source signer, gateway and controller retain the Stage 2 UID/DDS isolation.

Software rendering uses two Mesa worker threads per process to avoid
oversubscribing container CPU quotas. Sensor freshness and stop budgets remain
unchanged; rendering overload still fails closed rather than extending them.

Native person targets and calibration services run in a spawned process. Blocking Gazebo Transport
requests cannot hold the ROS world publisher or lidar callback's Python GIL.
Only the latest desired geometry is shared; measured lidar input and its original
200 ms freshness budget remain independent of the pose driver.

Native person geometry follows the latest target in a Gazebo system plugin.
The plugin interpolates all body parts on each physics step and caps root motion
at 0.24 m/s, so delayed transport targets cannot create catch-up jumps. It only
controls the twelve named person parts; robot control remains independent. Live
continuity checks pair measured native poses with their own source timestamps.

## Gazebo Transport boundary

The Docker launcher grants NET_ADMIN to the trusted root provisioner to install
container-local IPv4/IPv6 OUTPUT rules. All non-root traffic is restricted to
local IPv4 DDS UDP ports for the configured ROS domain (0..231, fewer than 120
participants). Every role and attack process starts after UID/capability drop,
with `no_new_privs` and an inherited libseccomp filter that denies external UNIX,
TCP, IPv6 and packet sockets and io_uring. Gazebo transport is unchanged for the
trusted root simulator and perception process. The normal ROS path must still
match and move the simulated robot. Missing support fails startup, without a
weaker fallback. Do not run this provisioner on the host.

`transport-isolation.json` contains root positive controls, actual Gazebo pose
request denials, forbidden UDP receiver observations and per-role inherited
restriction checks. The root pose request must change the real scan and then be
restored before any movement starts. This is a boundary for the sandboxed
container roles. Root/host/simulator compromise, process resource exhaustion,
remote Gazebo services and malicious use of the authorizer's signing authority
remain outside it. Relay command misuse is tested separately at M3 controllers. See [security scope](security-release.md).

## Repeated compound faults

Add `--compound-repeat 6` to a secured headless run. The viewer and CI each run six
iterations (two cycles). Each cycle requests 40, 80 and 100
positive velocity proposals per second while dropping actual lidar delivery
and SIGSTOP-delaying the world signer for 60, 140 and 220 ms respectively.
Every iteration requires approved measured motion and delivered signed
proposals and at least three accepted engine velocity decisions before injection, observes that the signer really stopped, keeps
proposals running after the fault, requires at least ten actual engine rejections
after zero, requires world-expiry zero within 400 ms,
and measures actual base stop. Fresh sensor recovery and another delivered
positive proposal must be received and rejected by the engine without rearming the source. The next iteration uses an
explicit fresh stop/rearm. Original world age remains 200 ms.

`compound-faults.json` records every iteration, observed proposal count and
latencies. Requested rates are bounded test loads, not guaranteed delivered
rates or a general denial-of-service defense. Run counts support repeatability;
they are not statistical worst-case or hardware evidence. Runtime failure
invalidates the whole run; no successful summary is produced.

The [simulator alpha guide](simulator-alpha.md) covers operator commands, report downloads, source/evidence packaging and supported scope.

## Evidence collection and timing scope

Role-owned logs and counters are read through directory descriptors anchored
at the trusted run root. Symlinks, hard links, special files, unexpected owners,
and logs above 256 MiB are rejected. Collection rejection cannot skip process
and transport cleanup. Exported diagnostics use ordinary 0644 permissions.

The local viewer now runs the same six compound iterations (two each at
40/80/100 proposals per second) required by the candidate report. Arm fault
reports include the measured lease renewal immediately before the fault and
require nonnegative stop delays. These delays describe the observed runs;
they are not a worst-case timing guarantee.
