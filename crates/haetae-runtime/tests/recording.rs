//! The dashcam recorder as a spec: what reaches the sillok log, and when.

mod common;

use std::fs;
use std::path::{Path, PathBuf};

use haetae_core::{Mode, Source, Verdict};
use haetae_runtime::{Fault, Inbound, RecorderConfig, Runtime, RuntimeConfig};
use serde_json::json;
use sillok::{Entry, Keypair};
use tempfile::TempDir;

use common::{decide, policy, proposal, world, NOW};

/// A runtime config recording to a fresh log in `dir`; returns the config,
/// the log path and the verifying key for `sillok::verify`.
fn recorder(dir: &TempDir, post_window: usize) -> (RuntimeConfig, PathBuf, String) {
    let key = Keypair::generate().unwrap();
    let vk = key.verifying_key_hex();
    let path = dir.path().join("log.jsonl");
    let mut rec = RecorderConfig::new(path.clone(), key);
    rec.post_window = post_window;
    let cfg = RuntimeConfig {
        recorder: Some(rec),
        ..RuntimeConfig::default()
    };
    (cfg, path, vk)
}

fn entries(path: &Path) -> Vec<Entry> {
    fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

#[test]
fn log_ts_is_recv_time_not_claimed_time() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, _vk) = recorder(&dir, 8);
    let mut rt = Runtime::new(policy(), None, cfg).unwrap();

    // The proposal claims t=111; it is received at t=9999, and its
    // missing-world denial is the incident that creates the log.
    let raw = json!({
        "id": 1,
        "source": "vla",
        "timestamp_ms": 111,
        "action": {"type": "move_to", "goal": {"x": 3.0, "y": 1.0}, "speed": 0.5},
    })
    .to_string();
    let d = decide(rt.handle_bytes(raw.as_bytes(), 9_999).unwrap());
    assert_eq!(d.fired, ["missing:world"]);
    rt.close().unwrap();

    let log = entries(&path);
    let p = log.iter().find(|e| e.kind == "proposal").unwrap();
    assert_eq!(p.ts_ms, 9_999); // trusted receive time
    assert_eq!(
        p.payload["proposal"]["timestamp_ms"].as_u64(),
        Some(111) // the claim stays inside the payload only
    );
    let d = log.iter().find(|e| e.kind == "decision").unwrap();
    assert_eq!(d.ts_ms, 9_999);
}

#[test]
fn rejects_alone_never_create_the_log() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, _vk) = recorder(&dir, 8);
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    rt.handle_bytes(b"garbage", NOW).unwrap();
    rt.handle_bytes(b"{\"world\": {}, \"fault\": {}}", NOW)
        .unwrap();
    rt.close().unwrap();

    assert!(!path.exists(), "rejects must not force log creation");
}

#[test]
fn rejects_are_logged_with_a_truncated_preview() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, _vk) = recorder(&dir, 8);
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    rt.handle_bytes(&vec![b'x'; 8192], NOW).unwrap();
    // a real incident flushes the buffered reject into the log
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 20.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    rt.close().unwrap();

    let log = entries(&path);
    let rej = log.iter().find(|e| e.kind == "reject").unwrap();
    assert_eq!(rej.ts_ms, NOW);
    assert!(rej.payload["error"].as_str().unwrap().contains("expected"));
    let input = rej.payload["input"].as_str().unwrap();
    assert!(
        input.len() <= 4096,
        "preview must be truncated: {}",
        input.len()
    );
}

#[test]
fn an_incident_flushes_the_sacho_backlog() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, _vk) = recorder(&dir, 8);
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    rt.handle(Inbound::World(world()), NOW).unwrap();
    decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    // replaying (vla, 1) is denied — and that denial is the incident
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.fired, ["replay:proposal"]);
    assert_eq!(rt.incidents(), 1);
    rt.close().unwrap();

    let log = entries(&path);
    let kinds: Vec<&str> = log.iter().map(|e| e.kind.as_str()).collect();
    // the whole backlog, oldest first, then the incident seal
    assert_eq!(
        kinds,
        ["world", "proposal", "decision", "proposal", "decision", "seal"]
    );
    let verdicts: Vec<&str> = log
        .iter()
        .filter(|e| e.kind == "decision")
        .map(|e| e.payload["verdict"].as_str().unwrap())
        .collect();
    assert_eq!(verdicts, ["yun", "bul"]);
}

#[test]
fn fault_into_stop_only_is_an_incident() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, _vk) = recorder(&dir, 8);
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    // caution is not stop-only: no incident, no log
    rt.handle(
        Inbound::Fault(Fault {
            code: "wobble".into(),
            timestamp_ms: NOW,
            raise_to: Mode::Caution,
        }),
        NOW,
    )
    .unwrap();
    assert_eq!(rt.incidents(), 0);
    assert!(!path.exists());

    rt.handle(
        Inbound::Fault(Fault {
            code: "estop-loop".into(),
            timestamp_ms: NOW,
            raise_to: Mode::SafePark,
        }),
        NOW,
    )
    .unwrap();
    assert_eq!(rt.incidents(), 1);
    assert!(path.exists());
    rt.close().unwrap();

    let log = entries(&path);
    let faults: Vec<&Entry> = log.iter().filter(|e| e.kind == "fault").collect();
    // both faults were in the sacho when the incident flushed it
    assert_eq!(faults.len(), 2);
    assert_eq!(faults[0].payload["mode_after"].as_str(), Some("caution"));
    assert_eq!(faults[1].payload["mode_before"].as_str(), Some("caution"));
    assert_eq!(faults[1].payload["mode_after"].as_str(), Some("safe_park"));
}

#[test]
fn post_window_counts_proposals_and_faults_then_seals() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, vk) = recorder(&dir, 2);
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    // the incident: a proposal outside the workspace
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 20.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert!(path.exists(), "an incident creates the log");

    // a world update and a reject inside the window are recorded but do
    // not consume it
    rt.handle(Inbound::World(world()), NOW + 1).unwrap();
    rt.handle_bytes(b"junk", NOW + 2).unwrap();

    // the two counting messages; the second seals the window
    rt.handle(proposal(2, Source::Vla, 3.0, 1.0, 0.5), NOW + 3)
        .unwrap();
    rt.handle(proposal(3, Source::Vla, 3.0, 1.0, 0.5), NOW + 4)
        .unwrap();
    // lands after the seal: buffered in the sacho, never persisted
    rt.handle(proposal(4, Source::Vla, 3.0, 1.0, 0.5), NOW + 5)
        .unwrap();
    rt.close().unwrap();

    let log = entries(&path);
    let kinds: Vec<&str> = log.iter().map(|e| e.kind.as_str()).collect();
    assert_eq!(
        kinds,
        [
            "proposal", "decision", "seal", // incident backlog + seal
            "world", "reject", // recorded, but do not consume
            "proposal", "decision", // window message 1
            "proposal", "decision", "seal" // window message 2 + closing seal
        ]
    );

    let prop_ids: Vec<u64> = log
        .iter()
        .filter(|e| e.kind == "proposal")
        .map(|e| e.payload["proposal"]["id"].as_u64().unwrap())
        .collect();
    assert_eq!(prop_ids, [1, 2, 3], "id 4 must not be persisted");

    let report = sillok::verify(&path, &vk).unwrap();
    assert!(report.fully_sealed());
}

#[test]
fn close_leaves_a_fully_sealed_log() {
    let dir = TempDir::new().unwrap();
    let (cfg, path, vk) = recorder(&dir, 8);
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    let d = decide(
        rt.handle(proposal(1, Source::Vla, 20.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    rt.close().unwrap();

    let report = sillok::verify(&path, &vk).unwrap();
    assert!(report.fully_sealed());
}
