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

> Status: **pre-alpha (0.0.x)**. Not a certified safety device.
