# Haetae (해태) /hɛ.tʰɛ/ "heh-teh"

**A permit gate for AI-driven robots: actuators accept only exactly-authorized, fresh commands, and incidents leave a verifiable record. Pre-alpha, not safety-rated.**

Haetae treats VLA / foundation-model output as *untrusted input*. Every proposed
robot action passes through a single policy gate that returns one of three verdicts:

| Verdict | 한글 | Meaning |
|---|---|---|
| `yun`  | 통과 | allow |
| `jeol` | 감속 | allow with a reduced speed cap |
| `bul`  | 차단 | deny |

This Python package currently ships only the `Verdict` type. The gate, the runtime loop and the
`sillok` signed incident log are implemented in Rust: https://github.com/haetae-robotics/haetae

> Status: **pre-alpha (0.0.x)**. Not a safety device.
