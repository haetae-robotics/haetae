# Haetae (해태) /hɛ.tʰɛ/ "heh-teh"

**Robot safety & security stack for physical AI.**

Haetae treats VLA / foundation-model output as *untrusted input*. Every proposed
robot action passes through a single policy gate that returns one of three verdicts:

| Verdict | Hanja | Meaning |
|---|---|---|
| `yun`  | 允 | allow |
| `jeol` | 節 | clamp (allow with reduced speed/force/workspace) |
| `bul`  | 不 | deny |

Planned components: `sillok` (tamper-evident incident recorder), `maek` (self-diagnosis),
`jangseung` (physical-space consent), `amhaeng` (red-team harness).

## Try it: the dinner-party attack

```bash
cargo build
PK=$(target/debug/haetae keygen --out key.seed)
target/debug/haetae judge \
  --policy examples/dinner-party/policy.json \
  --world examples/dinner-party/world.json \
  --proposals examples/dinner-party/proposals.jsonl \
  --sillok sillok.jsonl --key key.seed
target/debug/haetae sillok replay --log sillok.jsonl --pubkey "$PK"
```

The scenario has four parts:

- A compromised VLA tries to carry a knife toward a child.
- A spoofed tag sends the robot into the child's room at 2 m/s.
- A lidar fault raises the mode to `caution`, then to `hold`.
- Haetae denies (`bul`) or clamps (`jeol`) each unsafe step. The incident windows are sealed into a tamper-evident `sillok` log, which `replay` verifies before printing.

Layout: `crates/haetae-core` (gate), `crates/sillok` (recorder), `crates/haetae` (CLI).
Design contract: [`docs/w1-contract.md`](docs/w1-contract.md).

> Status: **pre-alpha (0.0.x)**. Not a certified safety device.
