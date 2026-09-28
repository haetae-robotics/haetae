# Reference deployment and validation

This is a ROS 2 Jazzy / Fast DDS reference setup for testing the Haetae gate.
It has no physical robot. The base is a Python kinematic controller and the arm
is a one-joint action server with its own 250 ms heartbeat stop. A real robot
needs its own measured controller contract and safety system before protective
use. See [the release gate](security-release.md).

## What runs where

| Principal | ROS enclave | Can write |
|---|---|---|
| AI/VLA process | `/haetae/vla` | `/vla/cmd_vel`, `/vla/arm` proposals |
| Trusted perception/health | `/haetae/world` | `/haetae_gate/world`, `/haetae_gate/fault` |
| Haetae bridge | `/haetae/gate` | `/cmd_vel`, gate state/outcome, arm action goals |
| Motor/arm controller | `/haetae/controller` | Arm action results; reads `/cmd_vel` |

`ros/security/haetae.policy.xml` defines these permissions. Keep each enclave's
private key with its own process account. The Python bridge's input signing
seeds are owner-only files. The policy, trust bundle and root public key must
be provisioned by an operator, separate from the AI process. No process that
can edit the bridge, its parameters or keys is considered untrusted by this
design.

## Reproduce the hosted software evidence

CI builds the locked Rust workspace, then runs these jobs under Jazzy with
`RMW_IMPLEMENTATION=rmw_fastrtps_cpp`:

- `ROS 2 reference scenarios`: eight base scenarios, five repeats each,
  `result.json`, `trajectory.csv`, `trajectory.svg` and signed sillok logs.
- `ROS 2 arm action reference`: malformed and out-of-bounds denial, replacement,
  tracking/human cancellation and gate-death behavior against a mock controller.
- `SROS2 permission generation`: generates signed permissions, then a VLA
  enclave attempts to publish directly to `/cmd_vel`, world and fault and call
  the arm action. The test requires authorized paths to work and protected
  receivers to reject the attempts.

The [CI workflow](../.github/workflows/ci.yml) uploads the base evidence as
`ros-e2e-evidence` and summarizes measurements in the job summary. Local Rust
and pure Python checks run without ROS:

```bash
cargo test --workspace --locked
python3 -m unittest discover -s ros/haetae_gate -p 'test_*.py'
python3 -m unittest discover -s ros/haetae_sim -p 'test_*.py'
python3 -m unittest discover -s ros/security -p 'test_*.py'
```

For a local Jazzy container with a running ROS graph, build `haetae` and run
the signed round trip and one scenario:

```bash
cargo build --release --locked -p haetae
source /opt/ros/jazzy/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DOMAIN_ID=77
python3 ros/smoke/enforce_roundtrip.py target/release/haetae
python3 ros/haetae_sim/run_scenario.py target/release/haetae 3 --out /tmp/haetae-scenario-3
python3 ros/haetae_sim/arm_smoke.py target/release/haetae
```

Use a separate `ROS_DOMAIN_ID` for each simultaneous run. The test fixtures
generate deterministic keys for reproducibility; never use those keys for a
robot.

## Create the reference SROS2 permissions

With Jazzy `sros2` installed, generate credentials outside the repository.
The CI job performs the same sequence:

```bash
source /opt/ros/jazzy/setup.bash
HAETAE_KEYSTORE=$(mktemp -d)
ros2 security create_keystore "$HAETAE_KEYSTORE"
for enclave in /haetae/gate /haetae/world /haetae/vla /haetae/controller; do
  ros2 security create_enclave "$HAETAE_KEYSTORE" "$enclave"
  ros2 security create_permission "$HAETAE_KEYSTORE" "$enclave" ros/security/haetae.policy.xml
done
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_STRATEGY=Enforce
export ROS_SECURITY_KEYSTORE="$HAETAE_KEYSTORE"
python3 ros/security/secure_probe.py orchestrate "$HAETAE_KEYSTORE"
```

Run each real node with its assigned `--ros-args -e /haetae/<role>` enclave.
The bridge requires a ROS parameter file with the policy, persistent state,
fresh sillok path, log key, trust bundle, root public key and role key paths.
Its `output_stamped` parameter defaults to `true` for `TwistStamped` on
`/cmd_vel`; set it to `false` only when the selected base controller expects
plain `Twist`. CI exercises both output types. The controller must accept the
chosen type directly, without an unaudited command forwarding node.
The reference `response_timeout_ms` is 500 ms because a positive command
waits for a durable state checkpoint. A configured controller must
independently stop on lost gateway heartbeat before that timeout; measure its
actual deadline and the worst checkpoint latency on the target host.
`ros/haetae_sim/run_scenario.py` creates a disposable example. Never reuse a
sillok path after a process restart; the log writer refuses to overwrite an
existing log. The persistent state path is deliberately reused so an unclean
restart enters Hold. An offline operator must use `haetae state set` to clear
Hold after verifying the robot is stopped and the cause is resolved.

## Before connecting motors

Measure the actual base controller's timeout, deceleration and command
latency, then set the policy from those measurements. Verify a physical motor
disable path works when the bridge and Rust process are killed. For an arm,
measure action cancellation acknowledgement, joint tracking, stop time and
gateway-death behavior; provide a robot-specific 3D collision and contact
model. Test the generated SROS2 permissions on that exact ROS graph and RMW.

An Arduino-class device can be a low-voltage bench target for heartbeat-loss
and motor-disable experiments. It does not establish that a robot arm or mobile
base is protected, and it does not replace a certified safety controller.

ROS references: [SROS2 permission policy format](https://github.com/ros2/design/blob/gh-pages/articles/181_ros2_access_control_policies.md),
[SROS2 enclave permissions workflow](https://github.com/ros2/sros2/blob/rolling/SROS2_MacOS.md),
[diff drive controller](https://control.ros.org/jazzy/doc/ros2_controllers/diff_drive_controller/doc/userdoc.html),
[joint trajectory controller](https://control.ros.org/jazzy/doc/ros2_controllers/joint_trajectory_controller/doc/userdoc.html).
