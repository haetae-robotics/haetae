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

## Components

- `haetae-core`: the policy gate (verdicts, envelope, rules, modes).
- `haetae-runtime`: transport-agnostic gate loop: world updates, faults that
  raise the mode, proposals judged at trusted receive time, incidents recorded.
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

Layout: `crates/haetae-core`, `crates/haetae-runtime`, `crates/sillok`, `crates/haetae`.
Design contracts: [`docs/w1-contract.md`](docs/w1-contract.md) (gate, sillok),
[`docs/w2-contract.md`](docs/w2-contract.md) (runtime).

> Status: **pre-alpha (0.0.x)**. Not a certified safety device.
