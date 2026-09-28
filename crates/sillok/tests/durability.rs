//! Crash behaviour: durable seals, private files, torn and empty logs.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::json;
use sillok::{verify, Keypair, SillokWriter, VerifyError};
use tempfile::TempDir;

/// A closed, fully sealed log of `n` notes; returns (dir, path, vk).
fn sealed_log(n: u64) -> (TempDir, PathBuf, String) {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("log.jsonl");
    let key = Keypair::from_seed([9; 32]);
    let vk = key.verifying_key_hex();
    let mut w = SillokWriter::create(&path, key, 64).unwrap();
    for i in 0..n {
        w.append(1_000 + i, "note", json!({ "i": i })).unwrap();
    }
    w.close().unwrap();
    (dir, path, vk)
}

fn append_raw(path: &Path, bytes: &[u8]) {
    OpenOptions::new()
        .append(true)
        .open(path)
        .unwrap()
        .write_all(bytes)
        .unwrap();
}

/// S1: a seal reaches the file before `seal()` returns. `mem::forget`
/// stands in for SIGKILL: no drop, no final flush, no close.
#[test]
fn seals_are_durable_without_close() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("log.jsonl");
    let key = Keypair::generate().unwrap();
    let vk = key.verifying_key_hex();
    let mut w = SillokWriter::create(&path, key, 64).unwrap();
    for i in 0..3 {
        w.append(i, "note", json!({ "i": i })).unwrap();
    }
    w.seal().unwrap();

    // Visible on disk while the writer is still alive.
    let report = verify(&path, &vk).unwrap();
    assert_eq!((report.entries, report.seals), (4, 1));
    assert!(report.is_complete());

    // A later unsealed append may be lost in the "crash"; the sealed
    // prefix never is.
    w.append(4, "note", json!({ "i": 4 })).unwrap();
    std::mem::forget(w);
    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.seals, 1);
    assert_eq!(report.entries - report.unsealed_tail, 4);
}

/// S4: logs hold people's positions; only the owner may read them.
#[cfg(unix)]
#[test]
fn log_is_created_owner_only() {
    use std::os::unix::fs::PermissionsExt;
    let (_dir, path, _vk) = sealed_log(1);
    let mode = fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o777, 0o600, "mode {mode:o}");
}

/// A directory the writer may create files in but not read (mode 0o300):
/// the directory fsync cannot be done, which must not leave a stuck,
/// half-created log. Either creation succeeds, or it fails and leaves
/// nothing behind so a retry can succeed.
#[cfg(unix)]
#[test]
fn unreadable_directory_never_leaves_an_orphan_log() {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let dir = TempDir::new().unwrap();
    if fs::metadata(dir.path()).unwrap().uid() == 0 {
        return; // root ignores directory permissions
    }
    let drop_dir = dir.path().join("drop");
    fs::create_dir(&drop_dir).unwrap();
    fs::set_permissions(&drop_dir, fs::Permissions::from_mode(0o300)).unwrap();
    let path = drop_dir.join("log.jsonl");
    let key = Keypair::generate().unwrap();
    let vk = key.verifying_key_hex();

    let created = SillokWriter::create(&path, key, 64).and_then(|mut w| {
        w.append(1, "note", json!({}))?;
        w.close()
    });
    let exists = path.exists();
    fs::set_permissions(&drop_dir, fs::Permissions::from_mode(0o700)).unwrap();
    match created {
        Ok(()) => assert!(verify(&path, &vk).unwrap().is_complete()),
        Err(e) => assert!(!exists, "create failed ({e}) but left {path:?} behind"),
    }
}

/// S3: a partial final line (a write cut short by a crash) is reported as
/// `torn_tail`; everything sealed before it still verifies.
#[test]
fn torn_final_line_is_reported_not_fatal() {
    // Cut mid-field, and cut inside a UTF-8 sequence (한 = ED 95 9C).
    for torn in [
        &br#"{"seq":4,"ts_ms":10"#[..],
        b"{\"seq\":4,\"kind\":\"\xed\x95",
    ] {
        let (_dir, path, vk) = sealed_log(3);
        let clean = verify(&path, &vk).unwrap();
        append_raw(&path, torn);

        let report = verify(&path, &vk).unwrap();
        assert!(report.torn_tail);
        assert_eq!(report.entries, clean.entries);
        assert_eq!(report.last_hash, clean.last_hash);
        assert!(report.fully_sealed(), "the entries that exist are sealed");
        assert!(!report.is_complete(), "but the file is not");
    }
}

/// S3: only the *final, unterminated* line gets that tolerance. Garbage
/// that ends in a newline, or sits before other lines, is still an error.
#[test]
fn malformed_terminated_or_inner_lines_still_fail() {
    let (_dir, path, vk) = sealed_log(2);
    append_raw(&path, b"{\"seq\":3\n");
    match verify(&path, &vk) {
        Err(VerifyError::Malformed { seq, .. }) => assert_eq!(seq, 3),
        other => panic!("terminated garbage must be Malformed, got {other:?}"),
    }

    let (_dir, path, vk) = sealed_log(2);
    let text = fs::read_to_string(&path).unwrap();
    let mut lines: Vec<&str> = text.lines().collect();
    lines.insert(1, "not json");
    // No trailing newline: the *last* line is fine, the inner one is not.
    fs::write(&path, lines.join("\n")).unwrap();
    match verify(&path, &vk) {
        Err(VerifyError::Malformed { seq, .. }) => assert_eq!(seq, 1),
        other => panic!("inner garbage must be Malformed, got {other:?}"),
    }
}

/// S3: the torn-tail tolerance covers only a write cut short (the JSON
/// ends early). An unterminated final line that is complete but invalid —
/// garbage, or a hand-edited entry with an extra field — is tampering or
/// corruption, not a crash, and stays `Malformed`.
#[test]
fn unterminated_invalid_final_line_is_malformed_not_torn() {
    let (_dir, path, _vk) = sealed_log(2);
    let text = fs::read_to_string(&path).unwrap();
    let last = text.lines().last().unwrap().to_owned();
    let extra_field = last.replacen('{', r#"{"x":1,"#, 1);

    for tail in ["xyz".to_owned(), "not json".to_owned(), extra_field] {
        let (_dir, path, vk) = sealed_log(2);
        append_raw(&path, tail.as_bytes());
        match verify(&path, &vk) {
            Err(VerifyError::Malformed { seq, .. }) => assert_eq!(seq, 3, "{tail}"),
            other => panic!("{tail:?} must be Malformed, got {other:?}"),
        }
    }
}

/// `verify_reader` over in-memory bytes gives the same verdict as `verify`
/// over the file — the entry point `sillok replay` uses to verify and
/// print from a single read.
#[test]
fn verify_reader_matches_verify() {
    let (_dir, path, vk) = sealed_log(3);
    append_raw(&path, br#"{"seq":4"#);
    let bytes = fs::read(&path).unwrap();

    let from_file = verify(&path, &vk).unwrap();
    let from_bytes = sillok::verify_reader(&bytes[..], &vk).unwrap();
    assert_eq!(from_bytes.entries, from_file.entries);
    assert_eq!(from_bytes.seals, from_file.seals);
    assert_eq!(from_bytes.last_hash, from_file.last_hash);
    assert!(from_bytes.torn_tail && from_file.torn_tail);

    let mut tampered = bytes.clone();
    let at = tampered.iter().position(|&b| b == b'1').unwrap();
    tampered[at] = b'2';
    assert!(sillok::verify_reader(&tampered[..], &vk).is_err());
}

/// A final line that lost only its newline is a complete entry and is
/// verified like any other.
#[test]
fn complete_final_line_without_newline_verifies() {
    let (_dir, path, vk) = sealed_log(2);
    let text = fs::read_to_string(&path).unwrap();
    fs::write(&path, text.trim_end_matches('\n')).unwrap();

    let report = verify(&path, &vk).unwrap();
    assert_eq!((report.entries, report.seals), (3, 1));
    assert!(!report.torn_tail);
    assert!(report.is_complete());
}

/// S2 (library side): an empty file is intact but explicitly incomplete.
#[test]
fn empty_file_verifies_but_is_not_complete() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("log.jsonl");
    fs::write(&path, b"").unwrap();
    let vk = Keypair::generate().unwrap().verifying_key_hex();

    let report = verify(&path, &vk).unwrap();
    assert_eq!(
        (report.entries, report.seals, report.unsealed_tail),
        (0, 0, 0)
    );
    assert!(!report.torn_tail);
    assert!(!report.fully_sealed());
    assert!(!report.is_complete());
}

/// A cloned key signs as the original (used to retry log creation).
#[test]
fn cloned_keypair_is_the_same_key() {
    let key = Keypair::generate().unwrap();
    let copy = key.clone();
    assert_eq!(copy.verifying_key_hex(), key.verifying_key_hex());
    assert_eq!(copy.seed_hex(), key.seed_hex());
}
