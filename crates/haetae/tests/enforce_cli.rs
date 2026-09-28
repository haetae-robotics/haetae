use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

use haetae_enforce::auth::{sign_bundle, sign_input, Role, SignedInput, TrustBody};
use sha2::{Digest, Sha256};
use sillok::Keypair;

fn signed(role: Role, counter: u64, payload: serde_json::Value, seed: [u8; 32]) -> String {
    let input = SignedInput {
        v: 1,
        audience: "test-base".into(),
        epoch: 1,
        counter,
        role,
        payload: payload.to_string(),
        signature: String::new(),
    };
    serde_json::to_string(&sign_input(input, &hex::encode(seed)).unwrap()).unwrap()
}

fn exchange(
    input: &mut std::process::ChildStdin,
    output: &mut BufReader<std::process::ChildStdout>,
    t: u64,
    k: &str,
    data: &str,
) -> serde_json::Value {
    writeln!(input, "{}", serde_json::json!({"t":t,"k":k,"data":data})).unwrap();
    input.flush().unwrap();
    let mut line = String::new();
    output.read_line(&mut line).unwrap();
    serde_json::from_str(&line).unwrap()
}

#[test]
fn authenticated_stdio_refuses_unsigned_and_replayed_motion_then_restart_holds() {
    let dir = tempfile::tempdir().unwrap();
    let path = |name: &str| dir.path().join(name);
    let policy = serde_json::json!({
        "allowed_sources":["vla"],
        "freshness":{"world_max_age_ms":200,"proposal_max_age_ms":1000,"future_tolerance_ms":20},
        "envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
        "base":{"footprint_radius":0.25,"max_decel":1.0,"max_angular":1.0,"latency_ms":50,"max_ttl_ms":200}
    });
    let policy_bytes = policy.to_string().into_bytes();
    fs::write(path("policy.json"), &policy_bytes).unwrap();
    fs::write(path("log.key"), Keypair::from_seed([9; 32]).seed_hex()).unwrap();
    let root = Keypair::from_seed([1; 32]);
    let world_key = Keypair::from_seed([2; 32]);
    let fault_key = Keypair::from_seed([3; 32]);
    let vla_key = Keypair::from_seed([4; 32]);
    let body = TrustBody {
        v: 1,
        audience: "test-base".into(),
        epoch: 1,
        policy_sha256: hex::encode(Sha256::digest(&policy_bytes)),
        keys: BTreeMap::from([
            (Role::World, world_key.verifying_key_hex()),
            (Role::Fault, fault_key.verifying_key_hex()),
            (Role::Vla, vla_key.verifying_key_hex()),
        ]),
    };
    let bundle = sign_bundle(&serde_json::to_string(&body).unwrap(), &root.seed_hex()).unwrap();
    fs::write(path("trust.json"), serde_json::to_vec(&bundle).unwrap()).unwrap();
    let binary = env!("CARGO_BIN_EXE_haetae");
    assert!(Command::new(binary)
        .args([
            "state",
            "set",
            "--state",
            path("state.json").to_str().unwrap(),
            "--mode",
            "normal",
            "--by",
            "test",
            "--reason",
            "start"
        ])
        .status()
        .unwrap()
        .success());
    let spawn = |log: &str| {
        Command::new(binary)
            .args([
                "enforce",
                "--stdio",
                "--policy",
                path("policy.json").to_str().unwrap(),
                "--state",
                path("state.json").to_str().unwrap(),
                "--sillok",
                path(log).to_str().unwrap(),
                "--key",
                path("log.key").to_str().unwrap(),
                "--trust",
                path("trust.json").to_str().unwrap(),
                "--root-pubkey",
                &root.verifying_key_hex(),
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap()
    };
    let mut child = spawn("log1.jsonl");
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let world = serde_json::json!({"stamp_ms":1000,"robot":{"pose":{"x":5.0,"y":5.0},"yaw":0.0},"humans":[],"confidence":1.0});
    let zero =
        serde_json::json!({"id":1,"source":"vla","timestamp_ms":1000,"action":{"type":"stop"}});
    let move_cmd = serde_json::json!({"id":2,"source":"vla","timestamp_ms":1000,"action":{"type":"velocity","linear":0.5,"angular":0.0,"ttl_ms":200}});
    let w = signed(Role::World, 1, world, [2; 32]);
    exchange(&mut input, &mut output, 1000, "signed", &w);
    exchange(
        &mut input,
        &mut output,
        1000,
        "signed",
        &signed(Role::Vla, 1, zero, [4; 32]),
    );
    let motion = signed(Role::Vla, 2, move_cmd, [4; 32]);
    let answer = exchange(&mut input, &mut output, 1000, "signed", &motion);
    assert_eq!(answer["cmd"]["linear"], 0.5);
    writeln!(
        input,
        "{}",
        serde_json::json!({"t":1001,"k":"reject","reason":"malformed source command"})
    )
    .unwrap();
    input.flush().unwrap();
    let mut rejection = String::new();
    output.read_line(&mut rejection).unwrap();
    let rejected: serde_json::Value = serde_json::from_str(&rejection).unwrap();
    assert_eq!(rejected["cmd"]["linear"], 0.0);
    assert_eq!(rejected["stop"], "denied");
    assert_eq!(
        rejected["outcome"]["rejected"]["error"],
        "malformed source command"
    );
    let unsigned = exchange(&mut input, &mut output, 1001, "world", "{}");
    assert_eq!(unsigned["cmd"]["linear"], 0.0);
    assert!(unsigned["outcome"]["rejected"]["error"]
        .as_str()
        .unwrap()
        .contains("unsigned"));
    let replay = exchange(&mut input, &mut output, 1002, "signed", &motion);
    assert_eq!(replay["cmd"]["linear"], 0.0);
    assert!(replay["outcome"]["rejected"]["error"]
        .as_str()
        .unwrap()
        .contains("replayed"));
    child.kill().unwrap();
    child.wait().unwrap();
    let mut child = spawn("log2.jsonl");
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    writeln!(input, "{}", serde_json::json!({"t":1100,"k":"tick"})).unwrap();
    input.flush().unwrap();
    let mut line = String::new();
    output.read_line(&mut line).unwrap();
    let after: serde_json::Value = serde_json::from_str(&line).unwrap();
    assert_eq!(after["status"]["mode"], "hold");
    assert_eq!(after["cmd"]["linear"], 0.0);
    drop(input); // a killed bridge closes stdin; this is never a clean reset
    assert!(!child.wait().unwrap().success());
    let state: serde_json::Value =
        serde_json::from_slice(&fs::read(path("state.json")).unwrap()).unwrap();
    assert_eq!(state["running"], true);
    assert!(Command::new(binary)
        .args([
            "state",
            "set",
            "--state",
            path("state.json").to_str().unwrap(),
            "--mode",
            "normal",
            "--by",
            "test",
            "--reason",
            "offline replay check"
        ])
        .status()
        .unwrap()
        .success());
    let mut child = spawn("log3.jsonl");
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let replay_after_reset = exchange(&mut input, &mut output, 1100, "signed", &motion);
    assert_eq!(replay_after_reset["cmd"]["linear"], 0.0);
    assert!(replay_after_reset["outcome"]["rejected"]["error"]
        .as_str()
        .unwrap()
        .contains("replayed"));
    drop(input);
    assert!(!child.wait().unwrap().success());

    // Make state checkpointing fail only after the gate has received a valid
    // world and zero/re-arm command. The next signed motion must not appear on
    // stdout: a controller must never see motion without its replay counter on
    // disk. Moving the directory keeps the already-open log usable but makes
    // the state path inaccessible on both Unix CI and macOS.
    assert!(Command::new(binary)
        .args([
            "state",
            "set",
            "--state",
            path("state.json").to_str().unwrap(),
            "--mode",
            "normal",
            "--by",
            "test",
            "--reason",
            "checkpoint failure check"
        ])
        .status()
        .unwrap()
        .success());
    let mut child = spawn("log4.jsonl");
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let fresh_world = serde_json::json!({"stamp_ms":1200,"robot":{"pose":{"x":5.0,"y":5.0},"yaw":0.0},"humans":[],"confidence":1.0});
    exchange(
        &mut input,
        &mut output,
        1200,
        "signed",
        &signed(Role::World, 2, fresh_world, [2; 32]),
    );
    let fresh_zero =
        serde_json::json!({"id":3,"source":"vla","timestamp_ms":1200,"action":{"type":"stop"}});
    exchange(
        &mut input,
        &mut output,
        1200,
        "signed",
        &signed(Role::Vla, 3, fresh_zero, [4; 32]),
    );
    let moved_dir = dir.path().with_extension("checkpoint-unavailable");
    fs::rename(dir.path(), &moved_dir).unwrap();
    let fresh_move = serde_json::json!({"id":4,"source":"vla","timestamp_ms":1200,"action":{"type":"velocity","linear":0.5,"angular":0.0,"ttl_ms":200}});
    writeln!(
        input,
        "{}",
        serde_json::json!({"t":1200,"k":"signed","data":signed(Role::Vla, 4, fresh_move, [4; 32])})
    )
    .unwrap();
    input.flush().unwrap();
    let mut leaked_command = String::new();
    let read = output.read_line(&mut leaked_command).unwrap();
    let failed = !child.wait().unwrap().success();
    fs::rename(&moved_dir, dir.path()).unwrap();
    assert!(
        failed,
        "state checkpoint failure must terminate the enforcer"
    );
    assert_eq!(
        read, 0,
        "motion was emitted before counter checkpoint: {leaked_command}"
    );
}
