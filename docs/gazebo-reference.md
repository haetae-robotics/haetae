# Gazebo reference robot

This reference connects the Rust enforcer and ROS bridge to a Gazebo Harmonic
mobile manipulator through `gz_ros2_control`. Its differential-drive controller
receives Haetae's `/cmd_vel`; its joint trajectory controller receives Haetae's
`FollowJointTrajectory` action. Gazebo joint states and odometry feed the world
publisher. The one-joint arm is intentionally simple and has no 3D collision
policy in Haetae.
The runner remaps Haetae's `/cmd_vel` publisher directly to the controller's
`/diff_drive_base_controller/cmd_vel` input; it adds no forwarding node.

The test checks approved base motion, a human-triggered zero command and base
stop, out-of-bounds arm denial, arm action cancellation, a sealed incident log,
and the base controller's command timeout after the bridge is killed. The
human report is injected from test code, not inferred from a camera. The arm
action controller has **no independent gateway-loss stop guarantee** in this
setup; only the base deadman is tested after gate death.

## Run headlessly on Ubuntu 24.04

Use ROS 2 Jazzy and Gazebo Harmonic. The official supported pairing is
documented by [Gazebo](https://gazebosim.org/docs/harmonic/ros2_integration/)
and [gz_ros2_control](https://control.ros.org/jazzy/doc/gz_ros2_control/doc/index.html).
The [CI job](../.github/workflows/ci.yml) uses the same package set:

```bash
sudo apt-get update
sudo apt-get install -y python3-cryptography ros-jazzy-rmw-fastrtps-cpp \
  ros-jazzy-ros-gz ros-jazzy-gz-ros2-control ros-jazzy-ros2-controllers \
  ros-jazzy-controller-manager ros-jazzy-robot-state-publisher ros-jazzy-xacro
source /opt/ros/jazzy/setup.bash
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
ros/gazebo/run_docker.sh
```

The first run builds a ROS 2 Jazzy and Gazebo Harmonic image and a Linux
Haetae binary for the host's CPU architecture. Open
`http://127.0.0.1:8765/` when the script prints the address. The simulator
waits for the live browser viewer before sending the first movement command.
The script writes `result.json` and diagnostic logs to
`artifacts/gazebo-local/`; pass another output directory as its first
argument if needed. Run the script again to repeat the scenario.

Docker publishes port 8765 on the host's `127.0.0.1` only. The container's
HTTP process listens on its own `0.0.0.0` so Docker can forward that local
port. Do not publish this test page on a public interface.

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
decisions. It is a live data view, not a capture of Gazebo's 3D GUI. The
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
(5, 5) spawn offset to odometry, and injects a person report during the test.
There is no real perception sensor, noise model, sensor attack test, SROS2
permission test on this expanded graph, or isolation of Gazebo Transport from
an attacker. The browser demo and Python kinematic fixture remain quicker
ways to explore policy behavior; this reference exercises actual ROS
controllers and Gazebo physics. Only physical hardware can establish a real
motor's stop time and distance or a real arm's cancel behavior.
