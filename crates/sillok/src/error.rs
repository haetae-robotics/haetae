//! Error type for key, writer and sacho operations.

use std::fmt;
use std::io;
use std::path::PathBuf;

/// Everything that can go wrong outside verification.
///
/// Verification failures have their own type, [`crate::VerifyError`], because
/// they must name the offending `seq`.
#[derive(Debug)]
#[non_exhaustive]
pub enum Error {
    /// Underlying I/O failure (open, write, flush, fsync).
    Io(io::Error),
    /// [`crate::SillokWriter::create`] was asked to use a path that already
    /// exists. Logs are never overwritten.
    AlreadyExists(PathBuf),
    /// The OS RNG failed while generating a key.
    Random(getrandom::Error),
    /// A seed string was not hex or did not decode to 32 bytes.
    BadSeed(String),
    /// `seal_every` must be at least 1.
    InvalidSealInterval,
    /// `kind: "seal"` is reserved; call [`crate::SillokWriter::seal`] instead
    /// of appending one by hand.
    ReservedKind,
    /// An entry could not be serialised. Unreachable in practice for
    /// well-formed [`crate::Entry`] values.
    Json(serde_json::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io(e) => write!(f, "i/o error: {e}"),
            Error::AlreadyExists(p) => write!(f, "log file already exists: {}", p.display()),
            Error::Random(e) => write!(f, "system RNG failed: {e}"),
            Error::BadSeed(why) => write!(f, "bad seed: {why}"),
            Error::InvalidSealInterval => write!(f, "seal_every must be at least 1"),
            Error::ReservedKind => write!(f, "kind \"seal\" is reserved; call seal()"),
            Error::Json(e) => write!(f, "entry serialisation failed: {e}"),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Error::Io(e) => Some(e),
            Error::Random(e) => Some(e),
            Error::Json(e) => Some(e),
            _ => None,
        }
    }
}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Json(e)
    }
}
