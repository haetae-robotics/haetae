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
/// when entries remain unsealed (or the log is empty).
///
/// Durability: every seal is flushed and fsynced (`sync_data`) before
/// [`SillokWriter::seal`] returns, so everything up to and including the
/// latest seal survives a crash or power loss. Plain appends stay in a
/// userspace buffer until the next seal — they are cheap, and a crash can
/// lose the unsealed tail (at worst leaving a torn final line, which
/// [`crate::verify`] reports as `torn_tail`). Dropping the writer without
/// `close()` still flushes buffered bytes, but skips the final seal and
/// fsync.
///
/// After any `Err` the in-memory chain state may no longer match the file
/// (a partial line may have been written). Discard the writer; never keep
/// appending through it.
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
    ///
    /// The log holds people's positions, so on unix it is created with mode
    /// `0o600` (owner read/write only; the umask can only narrow it). On
    /// unix the parent directory is fsynced as well, so the new directory
    /// entry itself survives a crash. That directory fsync is best-effort
    /// where it cannot be done at all — the directory is not readable
    /// (e.g. mode `0o300`) or the filesystem does not support syncing a
    /// directory — because the file's own seals are still fsynced. Any
    /// other directory-fsync failure is an error, and the empty file this
    /// call just created is removed first, so a retry is not blocked by
    /// [`Error::AlreadyExists`]. Other platforms get the default
    /// permissions and no directory fsync (std has no portable way to open
    /// a directory for syncing).
    pub fn create(path: impl AsRef<Path>, key: Keypair, seal_every: usize) -> Result<Self> {
        Self::create_with(path.as_ref(), key, seal_every, sync_parent_dir)
    }

    /// [`SillokWriter::create`] with the directory fsync injected, so tests
    /// can make it fail.
    fn create_with(
        path: &Path,
        key: Keypair,
        seal_every: usize,
        sync_dir: fn(&Path) -> std::io::Result<()>,
    ) -> Result<Self> {
        if seal_every == 0 {
            return Err(Error::InvalidSealInterval);
        }
        let mut opts = OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let file = opts.open(path).map_err(|e| match e.kind() {
            ErrorKind::AlreadyExists => Error::AlreadyExists(path.to_path_buf()),
            _ => Error::Io(e),
        })?;
        if let Err(e) = sync_dir(path) {
            // `create_new` guarantees the file is ours and still empty.
            // Best-effort: if removal fails too, the orphan stays behind.
            drop(file);
            let _ = std::fs::remove_file(path);
            return Err(Error::Io(e));
        }
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
    ///
    /// Returns only once the seal and everything before it are flushed and
    /// fsynced (`sync_data`): a sealed entry is a durable entry.
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
        self.file.flush()?;
        self.file.get_ref().sync_data()?;
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

/// Fsync the directory holding `path`, so a freshly created file's
/// directory entry is durable, not only its contents.
///
/// Skipped (returns `Ok`) when it cannot be done at all: the directory
/// cannot be opened for reading (`PermissionDenied` — creating a file needs
/// only write and search permission), or the filesystem rejects fsync on a
/// directory (`InvalidInput` / `Unsupported`, e.g. some network, FUSE or
/// FAT volumes). Other errors, such as an I/O error, are returned.
#[cfg(unix)]
fn sync_parent_dir(path: &Path) -> std::io::Result<()> {
    let dir = match path.parent() {
        Some(p) if !p.as_os_str().is_empty() => p,
        _ => Path::new("."),
    };
    let skippable = |e: &std::io::Error| {
        matches!(
            e.kind(),
            ErrorKind::PermissionDenied | ErrorKind::InvalidInput | ErrorKind::Unsupported
        )
    };
    match File::open(dir).and_then(|d| d.sync_all()) {
        Err(e) if !skippable(&e) => Err(e),
        _ => Ok(()),
    }
}

/// No-op: std cannot open a directory for syncing on this platform.
#[cfg(not(unix))]
fn sync_parent_dir(_path: &Path) -> std::io::Result<()> {
    Ok(())
}

/// Wall-clock timestamp for seal entries (0 if the clock is before epoch).
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Gazebo feedback contains tiny finite floats. Parsing a written log
    /// must recover the exact bits used to compute its hash.
    #[test]
    fn tiny_feedback_float_survives_write_verify() {
        let dir = tempfile::TempDir::new().expect("tempdir");
        let path = dir.path().join("log.jsonl");
        let key = Keypair::from_seed([9; 32]);
        let public = key.verifying_key_hex();
        let mut writer = SillokWriter::create(&path, key, 8).expect("writer");
        writer
            .append(
                100,
                "world",
                serde_json::json!({
                    "position": 1.0609417264943618e-18,
                    "velocity": 7.494866779476933e-19,
                    "yaw": 4.252223038681478e-17,
                    "x": 51.248178375505404,
                }),
            )
            .expect("append");
        writer.close().expect("seal");
        let report = crate::verify(&path, &public).expect("verify");
        assert!(report.is_complete());
    }

    /// A failing directory fsync must not leave an empty log behind that
    /// would make every retry fail with `AlreadyExists`.
    #[test]
    fn failed_dir_sync_removes_the_new_file() {
        let dir = tempfile::TempDir::new().expect("tempdir");
        let path = dir.path().join("log.jsonl");
        let key = Keypair::from_seed([7; 32]);
        let fail = |_: &Path| Err(std::io::Error::other("injected dir fsync failure"));

        let err = SillokWriter::create_with(&path, key.clone(), 64, fail);
        assert!(matches!(err, Err(Error::Io(_))));
        assert!(!path.exists(), "the half-created log was removed");

        // So a retry succeeds.
        SillokWriter::create(&path, key, 64)
            .and_then(SillokWriter::close)
            .expect("retry after a failed dir fsync");
        assert!(path.exists());
    }
}
