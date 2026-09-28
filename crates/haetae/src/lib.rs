//! Haetae (해태): a supervisory policy gate for AI-driven robots
//! (non-safety-rated, pre-alpha; not a certified safety function).
//!
//! Model output is untrusted. Every proposed action is judged by the gate
//! and receives exactly one [`Verdict`]. Decisions are kept in the `sacho`
//! ring buffer and sealed into the tamper-evident `sillok` log on incidents.

pub use haetae_core::*;
pub use haetae_runtime as runtime;
pub use sillok;
