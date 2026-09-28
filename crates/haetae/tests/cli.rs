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
    assert_eq!(out.status.code(), Some(0));
    let report: serde_json::Value = serde_json::from_str(&stdout(&out)).unwrap();
    assert_eq!(report["fully_sealed"], true);
    assert_eq!(report["complete"], true);
    assert_eq!(report["torn_tail"], false);

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
/// drop the fault. W2: it is rejected, and the run continues (per-message
/// isolation) instead of aborting.
#[test]
fn mixed_event_line_is_rejected_and_the_run_continues() {
    let mixed = r#"{"fault":{"code":"X","timestamp_ms":1,"raise_to":"hold"},"world":{"stamp_ms":1,"robot":{"pose":{"x":1,"y":1}},"confidence":0.9}}"#;
    let lines = [mixed, "not json at all", CALM].join("\n");
    let (_dir, _log, _pk, out) = judge_lines(&lines, "8");
    assert!(out.status.success());
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.contains(":1: rejected:") && err.contains("must be the only key"),
        "{err}"
    );
    assert!(err.contains(":2: rejected:"), "{err}");
    assert!(err.contains("rejected=2"), "{err}");
    // The valid line after the garbage was still judged.
    assert!(stdout(&out).contains(r#""proposal_id":2"#));
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

/// W2 review H2: an untrusted far-future timestamp must not move the file
/// adapter's clock and make every later line stale.
#[test]
fn forged_future_timestamp_does_not_poison_the_clock() {
    let forged = r#"{"id":1,"source":"vla","timestamp_ms":18446744073709551615,"action":{"type":"move_to","goal":{"x":3,"y":3},"speed":0.5}}"#;
    let lines = [forged, WORLD, CALM].join("\n");
    let (_dir, _log, _pk, out) = judge_lines(&lines, "8");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let decisions: Vec<serde_json::Value> = stdout(&out)
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(
        decisions[0]["fired"],
        serde_json::json!(["invalid:timestamp"])
    );
    assert_eq!(
        decisions[1]["verdict"], "yun",
        "later lines are still judged fresh"
    );
}

fn sillok_cmd(sub: &str, log: &Path, pubkey: &str) -> Output {
    haetae(&[
        "sillok",
        sub,
        "--log",
        log.to_str().unwrap(),
        "--pubkey",
        pubkey,
    ])
}

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

/// S2: an intact but incomplete log is "ok" but not "complete", warns on
/// stderr and exits 3 — for an empty file and for an unsealed tail.
#[test]
fn incomplete_logs_exit_3() {
    use haetae::sillok::{Keypair, SillokWriter};
    let dir = tempfile::tempdir().unwrap();
    let key = Keypair::generate().unwrap();
    let pubkey = key.verifying_key_hex();

    let empty = dir.path().join("empty.jsonl");
    fs::write(&empty, b"").unwrap();

    let tail = dir.path().join("tail.jsonl");
    let mut w = SillokWriter::create(&tail, key, 64).unwrap();
    w.append(1, "note", serde_json::json!({})).unwrap();
    w.seal().unwrap();
    w.append(2, "note", serde_json::json!({})).unwrap();
    drop(w);

    for (log, why) in [(&empty, "no entries"), (&tail, "not covered")] {
        let out = sillok_cmd("verify", log, &pubkey);
        assert_eq!(out.status.code(), Some(3), "{}", stderr(&out));
        let report: serde_json::Value = serde_json::from_str(&stdout(&out)).unwrap();
        assert_eq!(report["ok"], true);
        assert_eq!(report["complete"], false);
        let err = stderr(&out);
        assert!(err.contains("warning:") && err.contains(why), "{err}");

        let out = sillok_cmd("replay", log, &pubkey);
        assert_eq!(out.status.code(), Some(3));
        assert!(stderr(&out).contains(why));
    }
}

/// S2/S3: a torn final line leaves the sealed evidence verifiable, but the
/// log is incomplete (exit 3) and replay marks the torn tail.
#[test]
fn torn_tail_is_verified_marked_and_exits_3() {
    let run = run_dinner_party();
    let mut f = fs::OpenOptions::new().append(true).open(&run.log).unwrap();
    std::io::Write::write_all(&mut f, br#"{"seq":999,"ts_ms":17"#).unwrap();
    drop(f);

    let out = sillok_cmd("verify", &run.log, &run.pubkey);
    assert_eq!(out.status.code(), Some(3), "{}", stderr(&out));
    let report: serde_json::Value = serde_json::from_str(&stdout(&out)).unwrap();
    assert_eq!(report["ok"], true);
    assert_eq!(report["fully_sealed"], true);
    assert_eq!(report["torn_tail"], true);
    assert_eq!(report["complete"], false);
    assert!(stderr(&out).contains("torn"));

    let out = sillok_cmd("replay", &run.log, &run.pubkey);
    assert_eq!(out.status.code(), Some(3));
    let text = stdout(&out);
    assert!(
        text.contains("knife-near-child"),
        "sealed evidence still shown"
    );
    assert!(text.lines().last().unwrap().starts_with("TORN"), "{text}");
    assert!(stderr(&out).contains("torn"));
}

/// C1: strings from a log never reach the terminal as control sequences —
/// neither in the replay timeline nor in a verify error.
#[test]
fn replay_and_errors_escape_terminal_controls() {
    use haetae::sillok::{Entry, Keypair, SillokWriter, ZERO_HASH};
    let dir = tempfile::tempdir().unwrap();
    let key = Keypair::generate().unwrap();
    let pubkey = key.verifying_key_hex();
    let log = dir.path().join("evil.jsonl");
    let mut w = SillokWriter::create(&log, key, 64).unwrap();
    w.append(
        1,
        "reject",
        serde_json::json!({"error": "\u{1b}]0;pwned\u{7}\u{1b}[2J", "input": "x"}),
    )
    .unwrap();
    w.append(
        2,
        "evil\u{1b}[31m\u{9b}1m\u{7f}",
        serde_json::json!({"a": "\u{9b}"}),
    )
    .unwrap();
    w.close().unwrap();

    let out = sillok_cmd("replay", &log, &pubkey);
    assert_eq!(out.status.code(), Some(0), "{}", stderr(&out));
    let text = stdout(&out);
    for bad in ['\u{1b}', '\u{7}', '\u{9b}', '\u{7f}'] {
        assert!(
            !text.contains(bad),
            "raw {bad:?} reached the terminal: {text:?}"
        );
    }
    assert!(text.contains(r"\u{1b}]0;pwned\u{7}"), "{text}");
    assert!(text.contains(r"evil\u{1b}[31m\u{9b}1m\u{7f}"), "{text}");

    // A seal whose key_id carries ESC: the verify error quotes it, escaped.
    let forged = dir.path().join("forged.jsonl");
    let seal = Entry::new(
        0,
        0,
        "seal",
        serde_json::json!({"key_id": "\u{1b}[31mred", "sig": "00"}),
        ZERO_HASH,
    );
    fs::write(&forged, serde_json::to_string(&seal).unwrap() + "\n").unwrap();
    let out = sillok_cmd("verify", &forged, &pubkey);
    assert_eq!(out.status.code(), Some(1));
    let err = stderr(&out);
    assert!(!err.contains('\u{1b}'), "{err:?}");
    assert!(err.contains(r"\u{1b}[31mred"), "{err}");
}

/// R1: a recorder failure (here: the log path already exists) is reported
/// on stderr, every decision is still printed, the existing file is left
/// alone, and the run exits non-zero.
#[test]
fn judge_reports_recorder_failure_but_prints_every_decision() {
    let dir = tempfile::tempdir().unwrap();
    let key = dir.path().join("key.seed");
    let log = dir.path().join("sillok.jsonl");
    fs::write(&log, "precious\n").unwrap();
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
        example("proposals.jsonl").to_str().unwrap(),
        "--sillok",
        log.to_str().unwrap(),
        "--key",
        key.to_str().unwrap(),
    ]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(stdout(&out).lines().count(), 7, "every decision printed");
    let err = stderr(&out);
    assert!(err.contains("sillok recording failed"), "{err}");
    assert!(err.contains("already exists"), "{err}");
    assert_eq!(fs::read_to_string(&log).unwrap(), "precious\n");
}
