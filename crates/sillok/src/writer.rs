//! The append-only log writer (contract §2.1–2.3).

use std::fs::{File, OpenOptions};
use std::io::ErrorKind;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::entry::{hash_body_raw, seal_message, Entry, SEAL_KIND, ZERO_HASH};
use crate::error::Error;
use crate::keys::Keypair;
use crate::Result;

/// Append-only writer for a sillok log file.
///
/// [`SillokWriter::create`] refuses to overwrite an existing path — a log is
/// born once. A `seal` entry is written automatically after every
/// `seal_every` appended entries; [`SillokWriter::close`] adds a final one
/// when entries remain unsealed (or the log is empty), then fsyncs. Dropping
/// the writer without `close()` still flushes buffered bytes, but skips the
/// final seal and fsync.
///
/// TODO(W4): expose chain tips to an external anchor (RFC 3161 / Sigsum) so
/// truncation that removes a seal becomes detectable.
pub struct SillokWriter {
    file: BufWriter<File>,
    key: Keypair,
    seal_every: usize,
    next_seq: u64,
    /// Raw hash of the last written entry; all-zeros before the genesis entry.
    prev_hash: [u8; 32],
    /// Non-seal entries written since the last seal.
    since_seal: usize,
}

impl SillokWriter {
    /// Create a new log at `path`.
    ///
    /// Fails with [`Error::AlreadyExists`] if the file exists, and with
    /// [`Error::InvalidSealInterval`] if `seal_every` is 0.
    pub fn create(path: impl AsRef<Path>, key: Keypair, seal_every: usize) -> Result<Self> {
        if seal_every == 0 {
            return Err(Error::InvalidSealInterval);
        }
        let path = path.as_ref();
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|e| match e.kind() {
                ErrorKind::AlreadyExists => Error::AlreadyExists(path.to_path_buf()),
                _ => Error::Io(e),
            })?;
        Ok(SillokWriter {
            file: BufWriter::new(file),
            key,
            seal_every,
            next_seq: 0,
            prev_hash: [0; 32],
            since_seal: 0,
        })
    }

    /// Append one entry and return it (with its computed `hash`).
    ///
    /// When `seal_every` entries have been appended since the last seal, a
    /// seal entry is written afterwards automatically. `kind` must not be
    /// `"seal"` — use [`SillokWriter::seal`] (`"seal"` is a reserved kind and
    /// a hand-written one would fail verification).
    pub fn append(&mut self, ts_ms: u64, kind: &str, payload: Value) -> Result<Entry> {
        if kind == SEAL_KIND {
            return Err(Error::ReservedKind);
        }
        let entry = self.write_entry(ts_ms, kind, payload)?;
        self.since_seal += 1;
        if self.since_seal >= self.seal_every {
            self.seal()?;
        }
        Ok(entry)
    }

    /// Append a seal entry: an Ed25519 signature over the domain-separated
    /// message binding the seal's own `seq` and `ts_ms` plus the previous
    /// entry's hash (contract §2.2), plus the signing key's `key_id`.
    ///
    /// Safe to call anytime — including right after a seal or on an empty
    /// log (it signs `ZERO_HASH` then). The seal is itself an ordinary
    /// chained entry.
    pub fn seal(&mut self) -> Result<()> {
        let ts_ms = now_ms();
        let msg = seal_message(self.next_seq, ts_ms, &self.prev_hash);
        let sig = self.key.sign(&msg);
        let payload = json!({
            "sig": hex::encode(sig.to_bytes()),
            "key_id": self.key.key_id(),
        });
        self.write_entry(ts_ms, SEAL_KIND, payload)?;
        self.since_seal = 0;
        Ok(())
    }

    /// Write a final seal, flush, fsync, and close the file.
    ///
    /// The final seal is skipped when nothing was appended since the last
    /// seal (the last entry already is one). A completely empty log is still
    /// sealed, so every closed log ends in a signature.
    pub fn close(mut self) -> Result<()> {
        if self.since_seal > 0 || self.next_seq == 0 {
            self.seal()?;
        }
        self.file.flush()?;
        self.file.get_ref().sync_all()?;
        Ok(())
    }

    /// `seq` the next written entry will get — total lines written so far.
    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }

    /// Hash of the last written entry, lowercase hex ([`ZERO_HASH`] if none).
    pub fn last_hash(&self) -> String {
        if self.next_seq == 0 {
            ZERO_HASH.to_owned()
        } else {
            hex::encode(self.prev_hash)
        }
    }

    /// Entries written since the last seal — the currently unsealed tail.
    pub fn unsealed(&self) -> usize {
        self.since_seal
    }

    /// Serialise and write one entry, then advance the chain.
    fn write_entry(&mut self, ts_ms: u64, kind: &str, payload: Value) -> Result<Entry> {
        let prev = hex::encode(self.prev_hash);
        let raw = hash_body_raw(self.next_seq, ts_ms, kind, &payload, &prev);
        let entry = Entry {
            seq: self.next_seq,
            ts_ms,
            kind: kind.to_owned(),
            payload,
            prev,
            hash: hex::encode(raw),
        };
        serde_json::to_writer(&mut self.file, &entry)?;
        self.file.write_all(b"\n")?;
        self.prev_hash = raw;
        self.next_seq += 1;
        Ok(entry)
    }
}

/// Wall-clock timestamp for seal entries (0 if the clock is before epoch).
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
        .unwrap_or(0)
}
