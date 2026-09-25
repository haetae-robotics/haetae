//! haetae-runtime — the transport-agnostic gate loop of the Haetae robot
//! safety stack.
//!
//! The runtime owns a [`haetae_core::Gate`], the latest trusted
//! [`haetae_core::WorldSnapshot`], a replay detector and the dashcam
//! recorder. A transport — a JSONL file today, a ROS 2 node in W3 — feeds
//! bytes in with a trusted receive time `recv_ms` and gets an [`Outcome`]
//! back:
//!
//! - [`Inbound::World`] replaces the trusted world
//!   ([`Outcome::WorldUpdated`])
//! - [`Inbound::Fault`] raises the safety mode ([`Outcome::ModeChanged`])
//! - [`Inbound::Proposal`] is judged ([`Outcome::Decision`]). Before the
//!   gate runs, the runtime applies its own checks: `stop` is always
//!   allowed, proposals before the first snapshot are denied
//!   `missing:world`, and a repeated `(source, id)` is denied
//!   `replay:proposal`.
//! - unparseable bytes yield [`Outcome::Rejected`] — never an error, never
//!   an incident
//!
//! Recording is dashcam-style: every message goes into the `sacho` ring;
//! the first incident (a `Bul` decision, or a fault raising the mode into
//! stop-only) creates the sillok log, drains the backlog and seals it, then
//! records `post_window` more proposals/faults before sealing again.
//!
//! There is no clock inside: `recv_ms` is the only time source, and it must
//! share a domain with `WorldSnapshot::stamp_ms` (see
//! `docs/w2-contract.md` §1).

mod dedup;
mod error;
mod inbound;
mod outcome;
mod recorder;
mod runtime;

pub use error::RuntimeError;
pub use inbound::{Fault, Inbound};
pub use outcome::Outcome;
pub use runtime::{RecorderConfig, Runtime, RuntimeConfig};
