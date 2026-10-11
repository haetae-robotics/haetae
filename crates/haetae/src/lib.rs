//! A permit gate for AI-driven robots: actuators accept only exactly-authorized, fresh commands, and incidents leave a verifiable record. Pre-alpha, not safety-rated.
//!
//! Haetae (해태) is not a certified safety function. Model output is untrusted.
//! Every proposed action is judged by the gate and receives exactly one
//! [`Verdict`]. Decisions are kept in the `sacho` ring buffer and, on
//! incidents, sealed into the signed, hash-chained `sillok` log, which is
//! tamper-evident before external anchoring, only against adversaries without
//! host access.

pub use haetae_core::*;
pub use haetae_runtime as runtime;
pub use sillok;
