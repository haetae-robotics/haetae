//! Log verification (contract §2.4).
//!
//! [`verify`] replays a JSON Lines log and checks, per line:
//! well-formedness, `seq` continuity, the `prev` link, hash recomputation,
//! and — for `kind: "seal"` entries — `key_id` plus the Ed25519 signature
//! over the domain-separated seal message (contract §2.2).

use std::fmt;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::Value;

use crate::entry::{seal_message, Entry, ZERO_HASH};
use crate::keys::key_id_of;

/// Summary of a successfully verified log.
#[derive(Debug)]
pub struct VerifyReport {
    /// Total lines in the file, seals included.
    pub entries: u64,
    /// Lines with `kind == "seal"`.
    pub seals: u64,
    /// Entries after the last seal. Reported, never an error.
    pub unsealed_tail: u64,
    /// `hash` of the last entry — the chain tip ([`ZERO_HASH`] if empty).
    pub last_hash: String,
}

impl VerifyReport {
    /// `true` when the log holds at least one seal and leaves no unsealed
    /// tail (`seals > 0 && unsealed_tail == 0`).
    ///
    /// A log with *zero* seals verifies cleanly on hashes alone — the chain
    /// is internally consistent — but it proves nothing about who wrote it,
    /// since nothing was ever bound to a signing key.
    pub fn fully_sealed(&self) -> bool {
        self.seals > 0 && self.unsealed_tail == 0
    }
}

/// Why verification failed. Per-entry failures name the offending `seq`.
#[derive(Debug)]
#[non_exhaustive]
pub enum VerifyError {
    /// Could not open or read the file.
    Io(std::io::Error),
    /// The `verifying_key_hex` argument was not a valid Ed25519 key.
    BadKey(String),
    /// A line is not valid JSON or not a well-formed entry.
    Malformed { seq: u64, detail: String },
    /// `entry.seq` did not continue the chain (deleted or reordered entries).
    SeqGap { expected: u64, found: u64 },
    /// `entry.prev` did not equal the previous entry's `hash`.
    PrevMismatch {
        seq: u64,
        expected: String,
        found: String,
    },
    /// `entry.hash` did not match recomputation (fields were modified).
    HashMismatch {
        seq: u64,
        expected: String,
        found: String,
    },
    /// A seal's payload was malformed, named a different key, or its
    /// signature did not verify.
    BadSeal { seq: u64, detail: String },
}

impl fmt::Display for VerifyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            VerifyError::Io(e) => write!(f, "i/o error: {e}"),
            VerifyError::BadKey(why) => write!(f, "bad verifying key: {why}"),
            VerifyError::Malformed { seq, detail } => {
                write!(f, "entry seq {seq}: malformed line ({detail})")
            }
            VerifyError::SeqGap { expected, found } => {
                write!(
                    f,
                    "entry seq {expected}: found seq {found} — gap or reordering"
                )
            }
            VerifyError::PrevMismatch {
                seq,
                expected,
                found,
            } => write!(
                f,
                "entry seq {seq}: prev {found} != expected {expected} — broken chain"
            ),
            VerifyError::HashMismatch {
                seq,
                expected,
                found,
            } => write!(
                f,
                "entry seq {seq}: hash {found} != recomputed {expected} — modified entry"
            ),
            VerifyError::BadSeal { seq, detail } => {
                write!(f, "entry seq {seq}: bad seal ({detail})")
            }
        }
    }
}

impl std::error::Error for VerifyError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            VerifyError::Io(e) => Some(e),
            _ => None,
        }
    }
}

/// Verify a sillok log against `verifying_key_hex` (see
/// [`crate::Keypair::verifying_key_hex`]).
///
/// Returns a [`VerifyReport`] on success — including when entries follow the
/// last seal: such an *unsealed tail* is reported, not an error.
///
/// Local detection limits (contract §2.4.6): truncation that removes whole
/// sealed sections — seals included — still verifies cleanly, because the
/// remaining chain is internally consistent. Only truncation *after* a seal
/// shows up, as `unsealed_tail`. Binding seal hashes to an external witness
/// (anchoring) is the answer and lands in W4+.
// TODO(W4): compare the chain tip against externally anchored seals.
pub fn verify(
    path: impl AsRef<Path>,
    verifying_key_hex: &str,
) -> std::result::Result<VerifyReport, VerifyError> {
    let key = parse_key(verifying_key_hex)?;
    let key_id = key_id_of(&key);
    let reader = BufReader::new(File::open(path).map_err(VerifyError::Io)?);

    let mut entries = 0u64;
    let mut seals = 0u64;
    // Entries covered by the most recent seal (its seq + 1); `entries -
    // sealed_through` is the unsealed tail.
    let mut sealed_through = 0u64;
    let mut expected_prev = ZERO_HASH.to_owned();
    let mut last_hash = ZERO_HASH.to_owned();

    for line in reader.lines() {
        let line = line.map_err(VerifyError::Io)?;
        let seq = entries;
        let entry: Entry = serde_json::from_str(&line).map_err(|e| VerifyError::Malformed {
            seq,
            detail: e.to_string(),
        })?;

        if entry.seq != seq {
            return Err(VerifyError::SeqGap {
                expected: seq,
                found: entry.seq,
            });
        }
        if entry.prev != expected_prev {
            return Err(VerifyError::PrevMismatch {
                seq,
                expected: expected_prev,
                found: entry.prev.clone(),
            });
        }
        let recomputed = entry.computed_hash();
        if recomputed != entry.hash {
            return Err(VerifyError::HashMismatch {
                seq,
                expected: recomputed,
                found: entry.hash.clone(),
            });
        }
        if entry.is_seal() {
            check_seal(&entry, seq, &key, &key_id)?;
            seals += 1;
            sealed_through = seq + 1;
        }

        expected_prev = entry.hash.clone();
        last_hash = entry.hash;
        entries += 1;
    }

    Ok(VerifyReport {
        entries,
        seals,
        unsealed_tail: entries - sealed_through,
        last_hash,
    })
}

/// Check one seal entry's payload: `key_id` must name `key`, and `sig` must
/// be a valid signature over the seal's domain-separated message — the seal
/// entry's own `seq` and `ts_ms` plus the raw bytes of its `prev` hash
/// (contract §2.2).
fn check_seal(
    entry: &Entry,
    seq: u64,
    key: &VerifyingKey,
    key_id: &str,
) -> std::result::Result<(), VerifyError> {
    let bad = |detail: String| VerifyError::BadSeal { seq, detail };
    let get = |name: &str| -> std::result::Result<&str, VerifyError> {
        entry
            .payload
            .get(name)
            .and_then(Value::as_str)
            .ok_or_else(|| bad(format!("payload {name:?} missing or not a string")))
    };

    let claimed = get("key_id")?;
    if claimed != key_id {
        return Err(bad(format!("key_id {claimed} != expected {key_id}")));
    }

    let sig_hex = get("sig")?;
    let raw = hex::decode(sig_hex).map_err(|e| bad(format!("sig is not hex: {e}")))?;
    let sig_bytes: [u8; 64] = raw
        .as_slice()
        .try_into()
        .map_err(|_| bad(format!("sig must be 64 bytes, got {}", raw.len())))?;
    let sig = Signature::from_bytes(&sig_bytes);

    let prev = hex::decode(&entry.prev).map_err(|e| bad(format!("prev is not hex: {e}")))?;
    let prev_raw: [u8; 32] = prev
        .as_slice()
        .try_into()
        .map_err(|_| bad(format!("prev must be 32 bytes, got {}", prev.len())))?;
    let msg = seal_message(entry.seq, entry.ts_ms, &prev_raw);
    key.verify(&msg, &sig)
        .map_err(|e| bad(format!("signature does not verify: {e}")))
}

/// Decode `verifying_key_hex` into a [`VerifyingKey`].
fn parse_key(s: &str) -> std::result::Result<VerifyingKey, VerifyError> {
    let raw = hex::decode(s).map_err(|e| VerifyError::BadKey(format!("not hex: {e}")))?;
    let bytes: [u8; 32] = raw
        .as_slice()
        .try_into()
        .map_err(|_| VerifyError::BadKey(format!("expected 32 bytes, got {}", raw.len())))?;
    VerifyingKey::from_bytes(&bytes).map_err(|e| VerifyError::BadKey(e.to_string()))
}
