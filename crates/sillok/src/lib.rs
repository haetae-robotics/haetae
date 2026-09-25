//! sillok (실록, "veritable records") — the tamper-evident recorder of the
//! Haetae robot safety stack.
//!
//! A sillok log is a JSON Lines file where every line is an [`Entry`]. Each
//! entry carries the SHA-256 hash of its canonical body plus the hash of its
//! predecessor, forming a single-head hash chain. Every `seal_every` entries —
//! and on [`SillokWriter::close`] when entries remain unsealed — the writer
//! appends a `seal` entry holding an Ed25519 signature over a
//! domain-separated message binding the seal's `seq`, `ts_ms` and the chain
//! tip. [`verify`] replays a file, checking sequence, links, hashes and seal
//! signatures.
//!
//! [`Sacho`] is the companion in-memory ring buffer (the pre-trigger
//! window): push every observed record into it, and when an incident fires,
//! drain it into the real log with [`Sacho::flush_into`].
//!
//! Wire format and verification rules: `docs/w1-contract.md` §2.

mod entry;
mod error;
mod keys;
mod sacho;
mod verify;
mod writer;

pub use entry::{hash_body, Entry, SEAL_KIND, ZERO_HASH};
pub use error::Error;
pub use keys::Keypair;
pub use sacho::Sacho;
pub use verify::{verify, VerifyError, VerifyReport};
pub use writer::SillokWriter;

/// Result of key, writer and sacho operations.
pub type Result<T> = std::result::Result<T, Error>;
