//! Haetae (해태): robot safety & security stack for physical AI (pre-alpha).
//!
//! Model output is untrusted. Every proposed action is judged by the gate
//! and receives exactly one [`Verdict`]. Decisions are kept in the `sacho`
//! ring buffer and sealed into the tamper-evident `sillok` log on incidents.

pub use haetae_core::*;
pub use sillok;
