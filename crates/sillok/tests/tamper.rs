//! Tamper-detection tests — every item of contract §2.4 that must error.
//!
//! §2.4.6 (truncation that removes a seal is locally undetectable) is
//! documented on `sillok::verify` and intentionally untested.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use ed25519_dalek::{Signer, SigningKey};
use serde_json::{json, Value};
use sillok::{verify, Entry, Keypair, SillokWriter, VerifyError, SEAL_KIND};
use tempfile::TempDir;

/// Write a valid log of `n` "note" entries (auto-seal interval `seal_every`,
/// final seal on close); return (dir, path, signing seed, verifying key hex).
fn clean_log(n: u64, seal_every: usize) -> (TempDir, PathBuf, [u8; 32], String) {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("log.jsonl");
    let seed = [7u8; 32];
    let key = Keypair::from_seed(seed);
    let vk = key.verifying_key_hex();
    let mut w = SillokWriter::create(&path, key, seal_every).unwrap();
    for i in 0..n {
        w.append(1_000 + i, "note", json!({ "i": i })).unwrap();
    }
    w.close().unwrap();
    (dir, path, seed, vk)
}

fn lines(path: &Path) -> Vec<String> {
    fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(String::from)
        .collect()
}

fn rewrite(path: &Path, lines: &[String]) {
    fs::write(path, lines.join("\n") + "\n").unwrap();
}

/// §2.4.1 — a modified payload breaks the entry hash.
#[test]
fn detects_modified_payload() {
    let (_dir, path, _seed, vk) = clean_log(5, 64);
    let mut ls = lines(&path);
    let mut v: Value = serde_json::from_str(&ls[2]).unwrap();
    v["payload"]["i"] = json!(999);
    ls[2] = v.to_string();
    rewrite(&path, &ls);

    match verify(&path, &vk) {
        Err(VerifyError::HashMismatch { seq, .. }) => assert_eq!(seq, 2),
        Err(e) => panic!("expected HashMismatch at seq 2, got {e}"),
        Ok(_) => panic!("modified payload must not verify"),
    }
}

/// §2.4.2 — a deleted middle entry shows up as a seq gap.
#[test]
fn detects_deleted_entry() {
    let (_dir, path, _seed, vk) = clean_log(5, 64);
    let mut ls = lines(&path);
    ls.remove(2);
    rewrite(&path, &ls);

    match verify(&path, &vk) {
        Err(VerifyError::SeqGap { expected, found }) => {
            assert_eq!((expected, found), (2, 3));
        }
        Err(e) => panic!("expected SeqGap, got {e}"),
        Ok(_) => panic!("deleted entry must not verify"),
    }
}

/// §2.4.2 — a broken `prev` link is caught even if `seq` still lines up.
#[test]
fn detects_broken_prev_link() {
    let (_dir, path, _seed, vk) = clean_log(5, 64);
    let mut ls = lines(&path);
    let mut v: Value = serde_json::from_str(&ls[3]).unwrap();
    v["prev"] = json!("aa".repeat(32));
    ls[3] = v.to_string();
    rewrite(&path, &ls);

    match verify(&path, &vk) {
        Err(VerifyError::PrevMismatch { seq, .. }) => assert_eq!(seq, 3),
        Err(e) => panic!("expected PrevMismatch at seq 3, got {e}"),
        Ok(_) => panic!("broken prev link must not verify"),
    }
}

/// §2.4.3 — swapped entries break seq continuity at the first displaced line.
#[test]
fn detects_reordered_entries() {
    let (_dir, path, _seed, vk) = clean_log(5, 64);
    let mut ls = lines(&path);
    ls.swap(1, 2);
    rewrite(&path, &ls);

    match verify(&path, &vk) {
        Err(VerifyError::SeqGap { expected, found }) => {
            assert_eq!((expected, found), (1, 2));
        }
        Err(e) => panic!("expected SeqGap, got {e}"),
        Ok(_) => panic!("reordered entries must not verify"),
    }
}

/// §2.4.4 — a seal produced by a different key names a different `key_id`.
#[test]
fn detects_seal_from_wrong_key() {
    let (_dir, path, _seed, _vk) = clean_log(3, 64);
    let other = Keypair::generate().unwrap();

    match verify(&path, &other.verifying_key_hex()) {
        Err(VerifyError::BadSeal { seq, detail }) => {
            assert_eq!(seq, 3); // the close()-seal
            assert!(detail.contains("key_id"), "unexpected detail: {detail}");
        }
        Err(e) => panic!("expected BadSeal at seq 3, got {e}"),
        Ok(_) => panic!("seal from another key must not verify"),
    }
}

/// A 2-entry log (dropped without `close`, so no final seal) plus a forged,
/// chain-valid seal line appended by hand. `make_sig` gets the real signing
/// key and the raw 32-byte chain tip; returns (dir, path, verifying key).
fn log_with_forged_seal(
    make_sig: impl Fn(&SigningKey, &[u8; 32]) -> String,
) -> (TempDir, PathBuf, String) {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("log.jsonl");
    let key = Keypair::from_seed([7u8; 32]);
    let (vk, key_id) = (key.verifying_key_hex(), key.key_id());
    let mut w = SillokWriter::create(&path, key, 64).unwrap();
    w.append(1, "note", json!({})).unwrap();
    w.append(2, "note", json!({})).unwrap();
    let tip = w.last_hash();
    drop(w); // flush without a closing seal

    let tip_raw: [u8; 32] = hex::decode(&tip).unwrap().try_into().unwrap();
    let sig = make_sig(&SigningKey::from_bytes(&[7u8; 32]), &tip_raw);
    let forged = Entry::new(
        2,
        999_000,
        SEAL_KIND,
        json!({ "sig": sig, "key_id": key_id }),
        &tip,
    );
    let mut f = OpenOptions::new().append(true).open(&path).unwrap();
    writeln!(f, "{}", serde_json::to_string(&forged).unwrap()).unwrap();
    drop(f);
    (dir, path, vk)
}

/// Builds a forged `sig` hex from the signing key and the chain tip.
type SigForger = fn(&SigningKey, &[u8; 32]) -> String;

/// §2.4.5 — a forged seal: right `key_id`, but the signature does not verify
/// over the domain-separated seal message (contract §2.2).
#[test]
fn detects_forged_seal() {
    let cases: [(&str, SigForger); 3] = [
        // well-formed garbage
        ("garbage", |_, _| "0".repeat(128)),
        // a real signature over the wrong message
        ("wrong-message", |k, _| {
            hex::encode(k.sign(b"not the chain tip").to_bytes())
        }),
        // a valid signature over the bare chain tip — the unsigned-domain
        // shape a cross-protocol replay would carry
        ("bare-tip", |k, tip| hex::encode(k.sign(tip).to_bytes())),
    ];
    for (name, make_sig) in cases {
        let (_dir, path, vk) = log_with_forged_seal(make_sig);
        match verify(&path, &vk) {
            Err(VerifyError::BadSeal { seq, detail }) => {
                assert_eq!(seq, 2, "case {name}");
                assert!(detail.contains("verify"), "case {name}: {detail}");
            }
            Err(e) => panic!("case {name}: expected BadSeal at seq 2, got {e}"),
            Ok(_) => panic!("case {name}: forged seal must not verify"),
        }
    }
}

/// §2.4.8 — the seal signature covers the seal's own `ts_ms`, so re-dating
/// the last seal and recomputing its hash still breaks verification.
#[test]
fn detects_redated_last_seal() {
    let (_dir, path, _seed, vk) = clean_log(3, 64);
    let mut ls = lines(&path);
    let last = ls.len() - 1;
    let mut e: Entry = serde_json::from_str(&ls[last]).unwrap();
    assert!(e.is_seal());
    e.ts_ms += 60_000;
    e.hash = e.computed_hash();
    ls[last] = serde_json::to_string(&e).unwrap();
    rewrite(&path, &ls);

    match verify(&path, &vk) {
        Err(VerifyError::BadSeal { seq, .. }) => assert_eq!(seq, last as u64),
        Err(e) => panic!("expected BadSeal at seq {last}, got {e}"),
        Ok(_) => panic!("a re-dated last seal must not verify"),
    }
}

/// §2.4.7 — lines that are not JSON, miss required fields, or smuggle in
/// fields the hash does not cover.
#[test]
fn detects_malformed_line() {
    for (at, line) in [
        (1usize, "definitely not json".to_string()),
        (1, json!({"seq": 1}).to_string()),
        (0, {
            let mut v: Value = serde_json::from_str(
                &serde_json::to_string(&Entry::new(0, 0, "note", json!({}), &"0".repeat(64)))
                    .unwrap(),
            )
            .unwrap();
            v["unhashed_extra"] = json!(1);
            v.to_string()
        }),
    ] {
        let (_dir, path, _seed, vk) = clean_log(4, 64);
        let mut ls = lines(&path);
        ls[at] = line;
        rewrite(&path, &ls);

        match verify(&path, &vk) {
            Err(VerifyError::Malformed { seq, .. }) => assert_eq!(seq, at as u64),
            Err(e) => panic!("expected Malformed at seq {at}, got {e}"),
            Ok(_) => panic!("malformed line at seq {at} must not verify"),
        }
    }
}
