//! Behavioural tests: keys, writer sealing cadence, sacho ring, clean verify.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use sillok::{verify, Entry, Error, Keypair, Sacho, SillokWriter, VerifyError, ZERO_HASH};
use tempfile::TempDir;

fn log_in(dir: &TempDir) -> PathBuf {
    dir.path().join("log.jsonl")
}

fn entries(path: &Path) -> Vec<Entry> {
    fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

fn writer_at(path: &Path, seal_every: usize) -> (SillokWriter, String) {
    let key = Keypair::generate().unwrap();
    let vk = key.verifying_key_hex();
    let w = SillokWriter::create(path, key, seal_every).unwrap();
    (w, vk)
}

#[test]
fn keypair_seed_roundtrip() {
    let key = Keypair::generate().unwrap();
    let seed_hex = key.seed_hex();
    assert_eq!(seed_hex.len(), 64);
    assert_eq!(key.verifying_key_hex().len(), 64);
    assert_eq!(key.key_id().len(), 16);

    let from_hex = Keypair::from_seed_hex(&seed_hex).unwrap();
    assert_eq!(from_hex.verifying_key_hex(), key.verifying_key_hex());

    let raw: [u8; 32] = hex::decode(&seed_hex).unwrap().try_into().unwrap();
    let from_raw = Keypair::from_seed(raw);
    assert_eq!(from_raw.verifying_key_hex(), key.verifying_key_hex());
}

#[test]
fn bad_seeds_rejected() {
    assert!(matches!(
        Keypair::from_seed_hex("not hex"),
        Err(Error::BadSeed(_))
    ));
    assert!(matches!(
        Keypair::from_seed_hex(&"ab".repeat(31)),
        Err(Error::BadSeed(_))
    ));
}

#[test]
fn create_refuses_overwrite() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    SillokWriter::create(&path, Keypair::generate().unwrap(), 8)
        .unwrap()
        .close()
        .unwrap();

    match SillokWriter::create(&path, Keypair::generate().unwrap(), 8) {
        Err(Error::AlreadyExists(p)) => assert_eq!(p, path),
        Err(e) => panic!("expected AlreadyExists, got {e}"),
        Ok(_) => panic!("create must refuse to overwrite an existing log"),
    }
}

#[test]
fn seal_every_zero_rejected() {
    let dir = TempDir::new().unwrap();
    assert!(matches!(
        SillokWriter::create(log_in(&dir), Keypair::generate().unwrap(), 0),
        Err(Error::InvalidSealInterval)
    ));
}

#[test]
fn append_rejects_reserved_seal_kind() {
    let dir = TempDir::new().unwrap();
    let (mut w, _vk) = writer_at(&log_in(&dir), 64);
    assert!(matches!(
        w.append(0, "seal", json!({})),
        Err(Error::ReservedKind)
    ));
}

#[test]
fn writer_auto_seals_every_n() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 4);

    for i in 0..8 {
        w.append(100 + i, "tick", json!({ "i": i })).unwrap();
    }
    assert_eq!(w.next_seq(), 10); // 8 entries + 2 auto seals
    w.close().unwrap(); // no final seal: the last entry already is one

    let lines = entries(&path);
    assert_eq!(lines.len(), 10);
    assert!(lines[4].is_seal() && lines[9].is_seal());
    assert_eq!(
        lines.iter().map(|e| e.seq).collect::<Vec<_>>(),
        (0..10).collect::<Vec<_>>()
    );

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.entries, 10);
    assert_eq!(report.seals, 2);
    assert_eq!(report.unsealed_tail, 0);
}

#[test]
fn close_after_seal_writes_no_second_seal() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 64);
    w.append(1, "note", json!({})).unwrap();
    w.seal().unwrap();
    w.close().unwrap();

    let lines = entries(&path);
    assert_eq!(lines.len(), 2);
    assert!(lines[1].is_seal());

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.seals, 1);
    assert_eq!(report.unsealed_tail, 0);
    assert!(report.fully_sealed());
}

#[test]
fn close_writes_final_seal() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 64);
    for i in 0..3 {
        w.append(i, "note", json!({})).unwrap();
    }
    w.close().unwrap();

    let lines = entries(&path);
    assert_eq!(lines.len(), 4);
    assert!(lines[3].is_seal());

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.seals, 1);
    assert_eq!(report.unsealed_tail, 0);
}

#[test]
fn clean_log_reports_counts_and_chain() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 64);
    for i in 0..5 {
        w.append(10 + i, "note", json!({ "i": i })).unwrap();
    }
    w.close().unwrap();

    let lines = entries(&path);
    assert_eq!(lines[0].prev, ZERO_HASH);
    for (e, prev) in lines.iter().zip(&lines[1..]) {
        assert_eq!(prev.prev, e.hash);
        assert_eq!(prev.seq, e.seq + 1);
    }

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.entries, 6);
    assert_eq!(report.seals, 1);
    assert_eq!(report.unsealed_tail, 0);
    assert_eq!(report.last_hash, lines[5].hash);
}

#[test]
fn unsealed_tail_is_reported_not_an_error() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 64);
    w.append(1, "note", json!({})).unwrap();
    w.append(2, "note", json!({})).unwrap();
    w.seal().unwrap();
    for i in 0..3 {
        w.append(10 + i, "note", json!({})).unwrap();
    }
    let tip = w.last_hash();
    assert_eq!(w.unsealed(), 3);
    drop(w); // flushes without a final seal

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.entries, 6);
    assert_eq!(report.seals, 1);
    assert_eq!(report.unsealed_tail, 3);
    assert_eq!(report.last_hash, tip);
}

#[test]
fn empty_log_closes_to_single_seal() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (w, vk) = writer_at(&path, 64);
    assert_eq!(w.last_hash(), ZERO_HASH);
    w.close().unwrap();

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.entries, 1);
    assert_eq!(report.seals, 1);
    assert_eq!(report.unsealed_tail, 0);
}

#[test]
fn sacho_drops_oldest_when_full_and_flushes_in_order() {
    let mut sacho = Sacho::new(3);
    for i in 0..5 {
        sacho.push(i, &format!("k{i}"), json!({ "i": i }));
    }
    assert_eq!(sacho.len(), 3);

    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 64);
    let flushed = sacho.flush_into(&mut w).unwrap();
    assert_eq!(flushed, 3);
    assert!(sacho.is_empty());
    w.close().unwrap();

    let lines = entries(&path);
    assert_eq!(lines[0].kind, "k2");
    assert_eq!(lines[1].kind, "k3");
    assert_eq!(lines[2].kind, "k4");
    assert_eq!(lines[0].prev, ZERO_HASH);

    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.entries, 4);
}

/// A failed `append` inside `flush_into` must not lose the record it failed
/// on — it and everything after it stay buffered.
#[test]
fn sacho_flush_keeps_unwritten_records_on_error() {
    let mut sacho = Sacho::new(4);
    sacho.push(1, "note", json!({}));
    sacho.push(2, "seal", json!({})); // the writer rejects the reserved kind
    sacho.push(3, "note", json!({}));

    let dir = TempDir::new().unwrap();
    let (mut w, _vk) = writer_at(&log_in(&dir), 64);
    assert!(matches!(sacho.flush_into(&mut w), Err(Error::ReservedKind)));
    assert_eq!(w.next_seq(), 1);
    assert_eq!(sacho.len(), 2);
}

#[test]
fn sacho_zero_capacity_drops_everything() {
    let mut sacho = Sacho::new(0);
    sacho.push(1, "note", json!({}));
    assert_eq!(sacho.len(), 0);

    let dir = TempDir::new().unwrap();
    let (mut w, _vk) = writer_at(&log_in(&dir), 64);
    assert_eq!(sacho.flush_into(&mut w).unwrap(), 0);
    w.close().unwrap();
}

/// `fully_sealed` is true only when at least one seal exists and no tail is
/// left unsealed — a seal-free log verifies on hashes alone but proves
/// nothing about who wrote it.
#[test]
fn fully_sealed_flag() {
    let dir = TempDir::new().unwrap();

    let path = log_in(&dir);
    let (mut w, vk) = writer_at(&path, 64);
    w.append(1, "note", json!({})).unwrap();
    w.close().unwrap();
    assert!(verify(&path, &vk).unwrap().fully_sealed());

    let path = dir.path().join("tail.jsonl");
    let (mut w, vk) = writer_at(&path, 64);
    w.append(1, "note", json!({})).unwrap();
    w.seal().unwrap();
    w.append(2, "note", json!({})).unwrap();
    drop(w);
    let report = verify(&path, &vk).unwrap();
    assert_eq!((report.seals, report.unsealed_tail), (1, 1));
    assert!(!report.fully_sealed());

    let path = dir.path().join("bare.jsonl");
    let (mut w, vk) = writer_at(&path, 64);
    w.append(1, "note", json!({})).unwrap();
    drop(w);
    let report = verify(&path, &vk).unwrap();
    assert_eq!(report.seals, 0);
    assert!(!report.fully_sealed());
}

#[test]
fn verify_missing_file_and_bad_key() {
    let dir = TempDir::new().unwrap();
    let path = log_in(&dir);
    let vk = Keypair::generate().unwrap().verifying_key_hex();
    assert!(matches!(verify(&path, &vk), Err(VerifyError::Io(_))));

    let (_w, _vk) = writer_at(&path, 64);
    // not hex, wrong length, and a 32-byte string that is not a valid
    // compressed point all fail key parsing
    for bad in ["not hex".to_owned(), "ab".repeat(33), "ab".repeat(32)] {
        assert!(
            matches!(verify(&path, &bad), Err(VerifyError::BadKey(_))),
            "key {bad:?} should be rejected"
        );
    }
}

/// The canonical serialisation must be byte-identical to `serde_json::to_vec`
/// (contract §2.1), which `hash_body` relies on via `Value::to_string`.
#[test]
fn canonical_json_matches_to_vec() {
    let body: Value = json!({"z": 1, "a": {"y": [3, 2.5, "x"], "b": true}});
    assert_eq!(
        body.to_string().as_bytes(),
        serde_json::to_vec(&body).unwrap()
    );
}
