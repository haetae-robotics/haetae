//! Log entry model and canonical hashing (contract §2.1).

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// `kind` reserved for seal entries (contract §2.2). Never passed to
/// [`crate::SillokWriter::append`]; produced by `seal()` / `close()`.
pub const SEAL_KIND: &str = "seal";

/// `prev` of the genesis entry: 64 zeros.
pub const ZERO_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

/// One JSON Lines record in a sillok log.
///
/// Serialises with fields in the contract order
/// `seq, ts_ms, kind, payload, prev, hash`; `hash` is computed over the first
/// five only. Unknown fields are rejected on parse: anything the hash does
/// not cover must not be able to smuggle data into a verified log.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    /// Position in the chain; starts at 0 and increments by 1, no gaps.
    pub seq: u64,
    /// Milliseconds since the Unix epoch (caller-supplied wall clock).
    pub ts_ms: u64,
    /// Free-form kind: `"proposal"`, `"decision"`, `"fault"`, `"mode"`,
    /// `"note"`, or the reserved `"seal"`.
    pub kind: String,
    /// Opaque payload chosen by the caller.
    pub payload: Value,
    /// `hash` of the previous entry, or [`ZERO_HASH`] for `seq == 0`.
    pub prev: String,
    /// Lowercase-hex SHA-256 of the entry's canonical body (see [`hash_body`]).
    pub hash: String,
}

impl Entry {
    /// Build an entry, computing `hash` from the other fields.
    pub fn new(seq: u64, ts_ms: u64, kind: &str, payload: Value, prev: &str) -> Self {
        let hash = hash_body(seq, ts_ms, kind, &payload, prev);
        Entry {
            seq,
            ts_ms,
            kind: kind.to_owned(),
            payload,
            prev: prev.to_owned(),
            hash,
        }
    }

    /// Recompute the body hash from this entry's fields.
    pub fn computed_hash(&self) -> String {
        hash_body(self.seq, self.ts_ms, &self.kind, &self.payload, &self.prev)
    }

    /// Whether `kind` is the reserved `"seal"`.
    pub fn is_seal(&self) -> bool {
        self.kind == SEAL_KIND
    }
}

/// SHA-256 of an entry body's canonical JSON, as lowercase hex.
///
/// Canonical JSON is `serde_json::Value` (default `BTreeMap` maps, sorted
/// keys — `preserve_order` must stay disabled) serialised compactly.
/// `Value::to_string` emits exactly the bytes `serde_json::to_vec` produces,
/// and — unlike `to_vec` — cannot return an error.
pub fn hash_body(seq: u64, ts_ms: u64, kind: &str, payload: &Value, prev: &str) -> String {
    hex::encode(hash_body_raw(seq, ts_ms, kind, payload, prev))
}

/// Raw 32-byte body hash; the writer tracks the chain in this form.
pub(crate) fn hash_body_raw(
    seq: u64,
    ts_ms: u64,
    kind: &str,
    payload: &Value,
    prev: &str,
) -> [u8; 32] {
    let body = serde_json::json!({
        "seq": seq,
        "ts_ms": ts_ms,
        "kind": kind,
        "payload": payload,
        "prev": prev,
    });
    Sha256::digest(body.to_string().as_bytes()).into()
}

/// The domain-separated message a seal signs (contract §2.2):
/// `b"haetae/sillok/seal/v1\0" || seq || ts_ms || prev`, with `seq` and
/// `ts_ms` — the seal entry's own fields — as big-endian u64 and `prev` as
/// the raw 32 bytes of the previous entry's hash.
///
/// The tag stops the signature from being replayed into any other protocol
/// that shares the key, and covering `seq`/`ts_ms` binds the seal's own
/// fields: the last seal in a log is covered by no later entry or signature,
/// so without this it could be re-dated and re-hashed undetectably.
pub(crate) fn seal_message(seq: u64, ts_ms: u64, prev_raw: &[u8; 32]) -> Vec<u8> {
    const DOMAIN: &[u8] = b"haetae/sillok/seal/v1\0";
    let mut msg = Vec::with_capacity(DOMAIN.len() + 8 + 8 + 32);
    msg.extend_from_slice(DOMAIN);
    msg.extend_from_slice(&seq.to_be_bytes());
    msg.extend_from_slice(&ts_ms.to_be_bytes());
    msg.extend_from_slice(prev_raw);
    msg
}
