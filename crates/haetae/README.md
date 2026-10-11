# Haetae (해태) /hɛ.tʰɛ/ "heh-teh"

**A permit gate for AI-driven robots: actuators accept only exactly-authorized, fresh commands, and incidents leave a verifiable record. Pre-alpha, not safety-rated.**

Haetae treats VLA / foundation-model output as *untrusted input*. Every proposed
robot action passes through a single policy gate that returns one of three verdicts:

| Verdict | 한글 | Meaning |
|---|---|---|
| `yun`  | 통과 | allow as proposed |
| `jeol` | 감속 | allow with a reduced **speed** cap (the only limit Haetae clamps) |
| `bul`  | 차단 | deny |

Workspace and keep-out zone violations are denied (`bul`), never clamped.
Haetae does no force or torque limiting.

## What Haetae is and is not

Haetae is a **non-safety-rated supervisory layer**. It judges untrusted AI
commands *before* they reach the robot's own certified safety layer (safety
controller, safety-rated monitored stop, speed and separation monitoring). It
adds defense in depth; it does not replace that layer.

Haetae is **not** a certified safety function and must not be the only thing
between a model and a person. Current limits:

- **Reference enforcement only.** A Rust watchdog and ROS 2 bridge can command
  a simulated differential base and a mock arm action server. No real robot
  controller or target ROS graph has been validated.
- **Incomplete physical model.** The base model uses a 2D footprint and
  braking horizon. The arm checks short joint trajectories and tracking,
  without 3D link geometry, contact, force or torque protection.
- **Deployment boundary still required.** The Rust enforcer verifies role-bound
  signed inputs, rechecks active commands and persists raised modes. A real
  deployment still needs isolated keys and SROS2 permissions tested against its
  actual controllers and graph.

## This crate

The `haetae` CLI and library facade. It re-exports the gate (`haetae-core`),
the gate loop (`haetae::runtime`) and the incident recorder (`haetae::sillok`):

- `haetae keygen`: create a sillok signing key (secret seed file, public key on stdout).
- `haetae judge`: judge a JSON Lines stream of proposals, faults and world updates;
  incidents are recorded into a hash-chained, Ed25519-sealed `sillok` log.
- `haetae sillok verify` / `haetae sillok replay`: check a log's chain and seals, and
  print a verified, terminal-safe timeline.
- `haetae enforce --stdio`: run the signed-input watchdog behind a trusted
  transport such as the reference ROS 2 bridge.

Security scope and release blockers:
[`docs/security-release.md`](https://github.com/haetae-robotics/haetae/blob/main/docs/security-release.md).

## Try it

Build on macOS or Linux from a clone of the [repository](https://github.com/haetae-robotics/haetae) with git,
a C linker (Xcode Command Line Tools on macOS, `build-essential` on Debian/Ubuntu) and a current stable Rust
([rustup](https://rustup.rs)):

```bash
git clone https://github.com/haetae-robotics/haetae.git && cd haetae
cargo build
D=$(mktemp -d)
PK=$(target/debug/haetae keygen --out "$D/key.seed")
target/debug/haetae judge --policy examples/dinner-party/policy.json --world examples/dinner-party/world.json \
  --proposals examples/dinner-party/proposals.jsonl --sillok "$D/sillok.jsonl" --key "$D/key.seed"
target/debug/haetae sillok replay --log "$D/sillok.jsonl" --pubkey "$PK"
```

`$D` holds a secret key seed and a log with people's positions; delete it after use. `keygen` and log creation
never overwrite an existing file, so a rerun needs a fresh directory.

The scenario has four parts:

- Scripted adversarial proposal: a compromised VLA proposes carrying a knife toward a child.
- Scripted adversarial proposal: after a spoofed tag, the VLA proposes driving into the child's room at 2 m/s.
- Two lidar faults raise the mode to `caution`, then to `hold`.
  The replay marks these faults with `maek`, a log label, not a crate.
- Haetae denies (`bul`) or speed-clamps (`jeol`) each unsafe step. The incident windows are sealed into a signed,
  hash-chained `sillok` log, tamper-evident before external anchoring, only against adversaries without host access.
  `replay` verifies the log before printing.

These are verdicts only: `judge` drives nothing. `move_to` and `grasp` are judge-only actions that no controller
enforces, so the replay's `exec=` field shows what the gate allowed, not what a robot did. In the ROS 2 and Gazebo
references, only base velocity commands and arm joint trajectories are enforced.

## Browser simulator

`sim/` is an interactive 3D miniature-house simulator that runs the **real** `haetae-core` gate compiled to WebAssembly. The page only draws what the gate returns; it is a demo, not part of the gate.

- Pick an attack card (scripted adversarial proposals such as a knife toward a child, a spoofed tag or a hijacked peer robot) and watch Haetae judge it.
- Compare with "해태 없이 보기", the model's raw command executed unfiltered.
- Use the lab to send your own commands, inject faults, and edit the policy live.

The cards and the lab use judge-only actions (`move_to`, `grasp`, `place`): the robot animation draws the gate's
verdict, not an enforced stop.

```bash
rustup target add wasm32-unknown-unknown
cargo install wasm-bindgen-cli --version 0.2.129
sim/build.sh
python3 -m http.server -d sim 8000
```

The `wasm-bindgen-cli` version must match `Cargo.lock`. Then open http://localhost:8000/.

Layout: `crates/haetae-core` (gate), `crates/haetae-runtime` (gate loop), `crates/haetae-enforce` (watchdog), `crates/sillok` (recorder), `crates/haetae-wasm` (simulator bindings), `crates/haetae` (CLI).
Design contracts: [`docs/w1-contract.md`](https://github.com/haetae-robotics/haetae/blob/main/docs/w1-contract.md) (gate, sillok),
[`docs/w2-contract.md`](https://github.com/haetae-robotics/haetae/blob/main/docs/w2-contract.md) (runtime).

> Status: **pre-alpha (0.0.x)**. Not a safety device.
