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

## What Haetae is and is not

Haetae is a **non-safety-rated supervisory layer**. It judges untrusted AI
commands *before* they reach the robot's own certified safety layer (safety
controller, safety-rated monitored stop, speed and separation monitoring). It
adds defense in depth; it does not replace that layer.

Haetae is **not** a certified safety function and must not be the only thing
between a model and a person. Not done yet:

- **No enforcement point.** There is no ROS 2 node or driver integration yet;
  something else must actually refuse to execute what Haetae denies.
- **2D point robot model.** Positions, paths, zones and the workspace are 2D;
  arm geometry, reach and 3D obstacles are not modeled.
- **Judged only at admission.** A command is checked once, when it arrives.
  An approved action is not re-evaluated while it runs, even if the world changes.
- **Unauthenticated inputs.** Proposal `source` fields and world snapshots are
  taken at face value; nothing verifies who sent them.
- **Mode is not persisted.** A raised mode (`caution`, `hold`, E-stop) resets
  when the process restarts.

## This crate

The `haetae` CLI and library facade. It re-exports the gate (`haetae-core`),
the gate loop (`haetae::runtime`) and the incident recorder (`haetae::sillok`):

- `haetae keygen`: create a sillok signing key (secret seed file, public key on stdout).
- `haetae judge`: judge a JSON Lines stream of proposals, faults and world updates;
  incidents are recorded into a hash-chained, Ed25519-sealed `sillok` log.
- `haetae sillok verify` / `haetae sillok replay`: check a log's chain and seals, and
  print a verified, terminal-safe timeline.

Planned: `maek` (self-diagnosis), `jangseung` (physical-space consent),
`amhaeng` (red-team harness).

## Try it

From a checkout of the [repository](https://github.com/haetae-robotics/haetae):

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
fresh directory.

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
Design contracts: [`docs/w1-contract.md`](https://github.com/haetae-robotics/haetae/blob/main/docs/w1-contract.md) (gate, sillok),
[`docs/w2-contract.md`](https://github.com/haetae-robotics/haetae/blob/main/docs/w2-contract.md) (runtime).

> Status: **pre-alpha (0.0.x)**. Not a certified safety device.
