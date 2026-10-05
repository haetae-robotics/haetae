<picture>
  <source media="(prefers-color-scheme: dark)" srcset="sim/brand/haetae/logo-light.svg">
  <img src="sim/brand/haetae/logo.svg" alt="haetae" width="240">
</picture>

# Haetae (해태) /hɛ.tʰɛ/ "heh-teh"

**A supervisory policy gate for AI-driven robots.**

Haetae treats VLA / foundation-model output as *untrusted input*. Every proposed
robot action passes through a single policy gate that returns one of three verdicts:

| Verdict | 한글 | Meaning |
|---|---|---|
| `yun`  | 통과 | allow as proposed |
| `jeol` | 감속 | allow with a reduced **speed** cap (the only limit Haetae clamps) |
| `bul`  | 차단 | deny |

Workspace and keep-out zone violations are denied (`bul`), never clamped.
Haetae does no force or torque limiting.

For the UNO R4 Minima, the separate [controller permit LED reference](docs/controller-permits.md)
verifies authenticated action permits inside the MCU. It is an LED bench,
not a real robot protection release.

## What Haetae is and is not

Haetae is a **non-safety-rated supervisory layer**. It judges untrusted AI
commands *before* they reach the robot's own certified safety layer (safety
controller, safety-rated monitored stop, speed and separation monitoring). It
adds defense in depth; it does not replace that layer.

Haetae is **not** a certified safety function and must not be the only thing
between a model and a person. Not done yet:

- **Enforcement is under development.** A Rust watchdog and an rclpy bridge can
  send monitored base commands and short arm action chunks in a local reference
  setup. No target robot controller or production SROS2 deployment has been validated.
- **2D disc base model.** Base paths, zones and the workspace are 2D and include
  a configurable footprint radius. Arm geometry, reach and 3D obstacles are
  not modeled.
- **Arm geometry remains incomplete.** Short joint trajectories have bounds,
  rate and tracking checks, but there is no 3D link or contact model.
- **The deployment boundary is not established.** Signed inputs and a signed
  trust bundle cover the Rust subprocess. The ROS graph still needs tested
  SROS2 permissions and isolated keys on a target robot.

## Components

- `haetae-core`: the policy gate (verdicts, envelope, rules, modes).
- `haetae-runtime`: transport-agnostic gate loop: world updates, faults that
  raise the mode, proposals judged at trusted receive time, incidents recorded.
- `haetae-enforce`: watchdog, re-arm latch, persistent mode and signed-input
  verifier for short base and arm commands.
- `sillok` (실록): hash-chained, Ed25519-sealed incident log with `verify` and `replay`.
- `haetae`: the CLI (`keygen`, `judge`, `sillok verify`, `sillok replay`).

Planned: `maek` (self-diagnosis), `jangseung` (physical-space consent),
`amhaeng` (red-team harness).

## Try it: the dinner-party attack

```bash
cargo build
D=$(mktemp -d)   # the key seed is secret and the log holds people's positions
PK=$(target/debug/haetae keygen --out "$D/key.seed")
target/debug/haetae judge \
  --policy examples/dinner-party/policy.json \
  --world examples/dinner-party/world.json \
  --proposals examples/dinner-party/proposals.jsonl \
  --sillok "$D/sillok.jsonl" --key "$D/key.seed"
target/debug/haetae sillok replay --log "$D/sillok.jsonl" --pubkey "$PK"
```

`keygen` and log creation never overwrite an existing file, so a rerun needs a
fresh directory (run the block again from `D=$(mktemp -d)`).

The scenario has four parts:

- A compromised VLA tries to carry a knife toward a child.
- A spoofed tag sends the robot into the child's room at 2 m/s.
- A lidar fault raises the mode to `caution`, then to `hold`.
- Haetae denies (`bul`) or speed-clamps (`jeol`) each unsafe step. The incident
  windows are sealed into a tamper-evident `sillok` log, which `replay` verifies
  before printing.

## Browser simulator

`sim/` is an interactive 3D miniature-house simulator that runs the **real** `haetae-core` gate compiled to WebAssembly. The page only draws what the gate returns; it is a demo, not part of the gate.

- Pick an attack card (knife toward a child, spoofed tag, hijacked peer robot, …) and watch Haetae judge it.
- Compare with "해태 없이 보기", the model's raw command executed unfiltered.
- Use the lab to send your own commands, inject faults, and edit the policy live.

```bash
rustup target add wasm32-unknown-unknown
cargo install wasm-bindgen-cli --version 0.2.129   # must match Cargo.lock
sim/build.sh
python3 -m http.server -d sim 8000                  # then open http://localhost:8000/
```

Layout: `crates/haetae-core` (gate), `crates/haetae-runtime` (gate loop), `crates/sillok` (recorder), `crates/haetae-wasm` (simulator bindings), `crates/haetae` (CLI).
Design contracts: [`docs/w1-contract.md`](docs/w1-contract.md) (gate, sillok),
[`docs/w2-contract.md`](docs/w2-contract.md) (runtime).

Security scope and release blockers: [`docs/security-release.md`](docs/security-release.md).
Reference setup and test commands: [`docs/reference-deployment.md`](docs/reference-deployment.md).
Private vulnerability reports: [`SECURITY.md`](SECURITY.md).
The reference ROS base simulator and arm action test live in `ros/haetae_sim/`;
the SROS2 policy template is `ros/security/haetae.policy.xml`. Their CI results
measure only the simulated controller and hosted ROS setup.

For a physics-backed virtual robot, `ros/gazebo/` connects the same gate to a
Gazebo Harmonic **Husarion ROSbot XL + ROBOTIS OpenMANIPULATOR-X** through real
`ros2_control` controllers (four standard wheels and four monitored arm joints).
The manufacturer URDF and meshes are pinned locally with their licenses. Its [live browser view](sim/gazebo-live.html) shows the Gazebo 3D
window beside streamed measurements and decisions while the reference runs.
On macOS or Linux with Docker, run
`./haetae-demo start`, wait for the readiness message, then open
`http://127.0.0.1:8765/` and press **시뮬레이션 시작** when the robot appears. The
page also shows direct-command and forged-world attacks from a restricted
ROS node on the same Gazebo graph. The replay check uses a fresh production
enforcer. See the
[Gazebo scope and evidence](docs/gazebo-reference.md) before treating those
probe results as deployment evidence.
The [Gazebo runbook](docs/gazebo-reference.md) has the native Ubuntu command. The
[recorded replay](sim/gazebo-replay.html) works without Gazebo installed. These
references still cannot establish a real robot's stop behavior or arm
collision safety.

> Status: **simulator evaluation alpha; core 0.0.x**. Not a certified safety device or a protective physical-robot deployment.

A separate [household hazard lab](examples/household-hazards/README.md) starts with
`./haetae-demo hazards`: six end-effector fixture hazards and measured normal
controls, with explicit injected semantics and scope limits. This lab adds
trusted-adapter preflight checks; it is not physical safety qualification.

[Simple launch, verification and report guide](docs/simulator-alpha.md): `./haetae-demo doctor`, `start`, `status`, `stop`, `restart`, `verify`, `report`. Successful CI runs provide an unsigned source/evidence candidate with SHA256 checksums.

## UNO R4 LED benches

### H2 authenticated controller permits

`./haetae-permit verify` tests the actual Rust gate and controller crypto.
The [H2 setup guide](docs/controller-permits.md) covers private provisioning,
hardened USB firmware, physical USB qualification and LED sessions. The
controller checks each authenticated permit even if the relay is compromised.
This remains a built-in LED reference, without motor or electrical stop proof.

### H1 trusted USB legacy bench

`./haetae-bench verify` connects the actual signed Rust gate and the firmware
guard through virtual serial ports without a board. `./haetae-bench compile`
builds the UNO R4 Minima LED-only sketch using pinned Arduino tools.
[First board setup and fault tests](docs/uno-r4-bench.md) cover explicit USB
upload and LED sessions. Software results do not establish physical USB/watchdog
timing, motor stopping or protection of a real robot.

## Brand assets

The approved Haetae guardian and haetae-robotics kinematic identity are available as outlined SVGs, transparent PNGs, and app icons. See the [brand guide](docs/brand.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="sim/brand/haetae-robotics/logo-light.svg">
  <img src="sim/brand/haetae-robotics/logo.svg" alt="haetae-robotics" width="240">
</picture>
