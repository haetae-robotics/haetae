//! The runtime error type.

use std::fmt;

use haetae_core::PolicyError;

/// Everything that can fail inside [`crate::Runtime`].
///
/// Malformed inbound bytes are **not** errors — they become
/// [`crate::Outcome::Rejected`]. An `Err` means the safety machinery itself
/// failed: an invalid policy, an unusable config, or the recorder's I/O.
#[derive(Debug)]
#[non_exhaustive]
pub enum RuntimeError {
    /// [`haetae_core::Gate::new`] rejected the policy.
    Policy(PolicyError),
    /// The sillok recorder failed (log creation, append, seal or fsync).
    Sillok(sillok::Error),
    /// A [`crate::RuntimeConfig`] value is unusable.
    InvalidConfig(String),
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RuntimeError::Policy(e) => write!(f, "{e}"),
            RuntimeError::Sillok(e) => write!(f, "recorder error: {e}"),
            RuntimeError::InvalidConfig(m) => write!(f, "invalid config: {m}"),
        }
    }
}

impl std::error::Error for RuntimeError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            RuntimeError::Policy(e) => Some(e),
            RuntimeError::Sillok(e) => Some(e),
            RuntimeError::InvalidConfig(_) => None,
        }
    }
}

impl From<PolicyError> for RuntimeError {
    fn from(e: PolicyError) -> Self {
        RuntimeError::Policy(e)
    }
}

impl From<sillok::Error> for RuntimeError {
    fn from(e: sillok::Error) -> Self {
        RuntimeError::Sillok(e)
    }
}

impl From<serde_json::Error> for RuntimeError {
    /// Serialising a `Decision`/`WorldSnapshot` into a record payload is a
    /// recorder-path failure, so it is reported as [`sillok::Error::Json`].
    fn from(e: serde_json::Error) -> Self {
        RuntimeError::Sillok(e.into())
    }
}
