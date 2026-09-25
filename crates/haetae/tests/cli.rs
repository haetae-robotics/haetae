//! End-to-end: keygen → judge the dinner-party scenario → verify → tamper.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn example(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../examples/dinner-party")
        .join(name)
}

fn haetae(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_haetae"))
        .args(args)
        .output()
        .expect("binary runs")
}

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

struct Run {
    _dir: tempfile::TempDir,
    log: PathBuf,
    pubkey: String,
    decisions: Vec<serde_json::Value>,
}

fn run_dinner_party() -> Run {
    let dir = tempfile::tempdir().unwrap();
    let key = dir.path().join("key.seed");
    let log = dir.path().join("sillok.jsonl");

    let out = haetae(&["keygen", "--out", key.to_str().unwrap()]);
    assert!(out.status.success());
    let pubkey = stdout(&out).trim().to_string();

    let out = haetae(&[
        "judge",
        "--policy",
        example("policy.json").to_str().unwrap(),
        "--world",
        example("world.json").to_str().unwrap(),
        "--proposals",
        example("proposals.jsonl").to_str().unwrap(),
        "--sillok",
        log.to_str().unwrap(),
        "--key",
        key.to_str().unwrap(),
    ]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let decisions = stdout(&out)
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    Run {
        _dir: dir,
        log,
        pubkey,
        decisions,
    }
}

#[test]
fn dinner_party_verdicts() {
    let run = run_dinner_party();
    let verdicts: Vec<&str> = run
        .decisions
        .iter()
        .map(|d| d["verdict"].as_str().unwrap())
        .collect();
    assert_eq!(verdicts, ["yun", "yun", "bul", "bul", "jeol", "bul", "yun"]);
}

#[test]
fn recorded_log_verifies_and_replays() {
    let run = run_dinner_party();
    let out = haetae(&[
        "sillok",
        "verify",
        "--log",
        run.log.to_str().unwrap(),
        "--pubkey",
        &run.pubkey,
    ]);
    assert!(out.status.success());
    let report: serde_json::Value = serde_json::from_str(&stdout(&out)).unwrap();
    assert_eq!(report["fully_sealed"], true);

    let out = haetae(&[
        "sillok",
        "replay",
        "--log",
        run.log.to_str().unwrap(),
        "--pubkey",
        &run.pubkey,
    ]);
    assert!(out.status.success());
    let text = stdout(&out);
    assert!(text.contains("knife-near-child"));
    assert!(text.contains("maek M-LIDAR-022  mode caution → hold"));
    assert!(!text.contains("UNSEALED"));
}

#[test]
fn rewriting_a_verdict_is_detected() {
    let run = run_dinner_party();
    let original = fs::read_to_string(&run.log).unwrap();
    let forged = original.replacen(r#""verdict":"bul""#, r#""verdict":"yun""#, 1);
    assert_ne!(original, forged, "log contains a bul decision");
    fs::write(&run.log, forged).unwrap();

    let out = haetae(&[
        "sillok",
        "verify",
        "--log",
        run.log.to_str().unwrap(),
        "--pubkey",
        &run.pubkey,
    ]);
    assert!(!out.status.success());
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("seq"), "error names the entry: {err}");
}

#[test]
fn nothing_is_recorded_without_an_incident() {
    let dir = tempfile::tempdir().unwrap();
    let key = dir.path().join("key.seed");
    let log = dir.path().join("sillok.jsonl");
    let calm = dir.path().join("calm.jsonl");
    fs::write(
        &calm,
        r#"{"id":1,"source":"planner","timestamp_ms":1727241780000,"action":{"type":"move_to","goal":{"x":3,"y":3},"speed":0.5}}"#,
    )
    .unwrap();
    assert!(haetae(&["keygen", "--out", key.to_str().unwrap()])
        .status
        .success());

    let out = haetae(&[
        "judge",
        "--policy",
        example("policy.json").to_str().unwrap(),
        "--world",
        example("world.json").to_str().unwrap(),
        "--proposals",
        calm.to_str().unwrap(),
        "--sillok",
        log.to_str().unwrap(),
        "--key",
        key.to_str().unwrap(),
    ]);
    assert!(out.status.success());
    assert!(!log.exists());
}

fn judge_lines(lines: &str, post: &str) -> (tempfile::TempDir, PathBuf, String, Output) {
    let dir = tempfile::tempdir().unwrap();
    let key = dir.path().join("key.seed");
    let log = dir.path().join("sillok.jsonl");
    let input = dir.path().join("steps.jsonl");
    fs::write(&input, lines).unwrap();
    let out = haetae(&["keygen", "--out", key.to_str().unwrap()]);
    let pubkey = stdout(&out).trim().to_string();
    let out = haetae(&[
        "judge",
        "--policy",
        example("policy.json").to_str().unwrap(),
        "--world",
        example("world.json").to_str().unwrap(),
        "--proposals",
        input.to_str().unwrap(),
        "--sillok",
        log.to_str().unwrap(),
        "--key",
        key.to_str().unwrap(),
        "--post",
        post,
    ]);
    (dir, log, pubkey, out)
}

const INTO_CHILD_ROOM: &str = r#"{"id":1,"source":"vla","timestamp_ms":1727241780001,"action":{"type":"move_to","goal":{"x":8.5,"y":8.5},"speed":0.2}}"#;
const CALM: &str = r#"{"id":2,"source":"planner","timestamp_ms":1727241780002,"action":{"type":"move_to","goal":{"x":3,"y":3},"speed":0.2}}"#;
const WORLD: &str = r#"{"world":{"stamp_ms":1727241780002,"robot":{"pose":{"x":1,"y":1}},"humans":[],"confidence":0.9}}"#;

/// H3 (Devin's review): a line mixing `fault` and `world` must not silently
/// drop the fault.
#[test]
fn mixed_event_line_is_rejected() {
    let line = r#"{"fault":{"code":"X","timestamp_ms":1,"raise_to":"hold"},"world":{"stamp_ms":1,"robot":{"pose":{"x":1,"y":1}},"confidence":0.9}}"#;
    let (_dir, _log, _pk, out) = judge_lines(line, "8");
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("must be the only key"));
}

/// M3 (Devin's review): world updates do not use up the post-incident window.
#[test]
fn world_updates_do_not_starve_the_post_window() {
    let lines = [INTO_CHILD_ROOM, WORLD, WORLD, CALM].join("\n");
    let (_dir, log, pubkey, out) = judge_lines(&lines, "1");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );

    let text = fs::read_to_string(&log).unwrap();
    assert!(
        text.contains(r#""proposal_id":2"#),
        "post-incident decision recorded"
    );
    assert!(text.contains(r#""kind":"world""#), "world updates recorded");

    let out = haetae(&[
        "sillok",
        "verify",
        "--log",
        log.to_str().unwrap(),
        "--pubkey",
        &pubkey,
    ]);
    let report: serde_json::Value = serde_json::from_str(&stdout(&out)).unwrap();
    assert_eq!(report["fully_sealed"], true);
}
