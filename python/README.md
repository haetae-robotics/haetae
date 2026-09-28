# Haetae (해태) /hɛ.tʰɛ/ "heh-teh"

**A supervisory policy gate for AI-driven robots** (non-safety-rated, pre-alpha).

Haetae treats VLA / foundation-model output as *untrusted input*. Every proposed
robot action passes through a single policy gate that returns one of three verdicts:

| Verdict | 한글 | Meaning |
|---|---|---|
| `yun`  | 통과 | allow |
| `jeol` | 감속 | allow with a reduced speed cap |
| `bul`  | 차단 | deny |

This Python package currently ships only the `Verdict` type. The gate, the runtime loop and the
`sillok` signed incident log are implemented in Rust: https://github.com/haetae-robotics/haetae

> Status: **pre-alpha (0.0.x)**. Not a certified safety device.
