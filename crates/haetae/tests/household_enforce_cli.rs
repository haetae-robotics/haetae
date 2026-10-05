//! Contract tests through the real authenticated stdio execution boundary.
//! Fixed seeds and a synthetic root-attested FK chain are test fixtures only.
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use haetae_enforce::auth::{sign_bundle, sign_input, Role, SignedInput, TrustBody};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sillok::Keypair;

const AUDIENCE: &str = "household-cli-fixture";
const MODEL_SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ROOT_SEED: [u8; 32] = [1; 32];
const WORLD_SEED: [u8; 32] = [2; 32];
const FAULT_SEED: [u8; 32] = [3; 32];
const VLA_SEED: [u8; 32] = [4; 32];

fn signed(role: Role, counter: u64, payload: Value, seed: [u8; 32]) -> String {
    let input = SignedInput {
        v: 1,
        audience: AUDIENCE.into(),
        epoch: 1,
        counter,
        role,
        payload: payload.to_string(),
        signature: String::new(),
    };
    serde_json::to_string(&sign_input(input, &hex::encode(seed)).unwrap()).unwrap()
}

fn policy() -> Value {
    let joints: Vec<_> = (1..=4)
        .map(|i| {
            json!({"name":format!("joint{i}"),"min_position":-1.0,"max_position":1.0,
                   "max_velocity":1.0,"max_acceleration":40.0})
        })
        .collect();
    let mut chain: Vec<_> = (0..4)
        .map(|i| json!({"xyz":[0,0,0],"rpy":[0,0,0],"joint_index":i,"axis":"z"}))
        .collect();
    chain.push(json!({"xyz":[0.2,0,0],"rpy":[0,0,0]}));
    json!({
        "allowed_sources":["vla"],
        "freshness":{"world_max_age_ms":200,"proposal_max_age_ms":1000,"future_tolerance_ms":20},
        "envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
        "base":{"footprint_radius":0.25,"max_decel":1.0,"max_angular":1.0,"latency_ms":50,"max_ttl_ms":200},
        "arm":{"joints":joints,"max_points":8,"max_duration_ms":200,
               "max_start_error":0.01,"max_tracking_error":0.05,"min_confidence":0.9},
        "household":{"schema_version":1,"robot_id":"rosbot_xl_open_manipulator_x",
                     "model_sha256":MODEL_SHA,"tool_id":"household_fixture_v1",
                     "chain":chain,"sample_step_rad":0.002,"swept_radius_m":0.15}
    })
}

fn binding() -> Value {
    json!({"schema_version":1,"world_revision":1,"task_revision":1,
           "task_id":"test-task","step_id":"step-1",
           "robot_id":"rosbot_xl_open_manipulator_x","model_sha256":MODEL_SHA,
           "tool_id":"household_fixture_v1","item_id":"fixture-1"})
}

fn world(t: u64) -> Value {
    let joints: Vec<_> = (1..=4)
        .map(|i| json!({"name":format!("joint{i}"),"position":0.0,"velocity":0.0}))
        .collect();
    json!({"stamp_ms":t,"robot":{"pose":{"x":5,"y":5},"yaw":0.0,
           "twist":{"linear":0.0,"angular":0.0},"joints":joints},
           "humans":[],"confidence":1.0,
           "semantic":{"schema_version":1,"revision":1,"task_revision":1,
               "task_id":"test-task","step_id":"step-1",
               "robot_id":"rosbot_xl_open_manipulator_x","model_sha256":MODEL_SHA,
               "tool_id":"household_fixture_v1","item_id":"fixture-1",
               "observed_ms":t,"confidence":1.0,"coverage_known":true,"item":"inert",
               "base_pose":{"x":5,"y":5},"base_yaw":0.0,
               "regions":[{"id":"person-volume","kind":"human","state":"active",
                   "bounds":{"min":{"x":5.19,"y":4.82,"z":-0.02},
                             "max":{"x":5.21,"y":4.835,"z":0.02}},
                   "contents":[],"contents_known":true}]}})
}

fn stop(id: u64, t: u64) -> Value {
    json!({"id":id,"source":"vla","timestamp_ms":t,"action":{"type":"stop"}})
}

fn trajectory(id: u64, t: u64, target: f64) -> Value {
    json!({"id":id,"source":"vla","timestamp_ms":t,"semantic":binding(),
           "action":{"type":"joint_trajectory","ttl_ms":200,"points":[
               {"time_from_start_ms":0,"positions":[0.0,0.0,0.0,0.0]},
               {"time_from_start_ms":100,"positions":[target,0.0,0.0,0.0]},
               {"time_from_start_ms":200,"positions":[target,0.0,0.0,0.0]}]}})
}

struct Fixture {
    dir: tempfile::TempDir,
    root_pubkey: String,
}

impl Fixture {
    fn new() -> Self {
        let fixture = Self {
            dir: tempfile::tempdir().unwrap(),
            root_pubkey: Keypair::from_seed(ROOT_SEED).verifying_key_hex(),
        };
        let bytes = serde_json::to_vec(&policy()).unwrap();
        fs::write(fixture.path("policy.json"), &bytes).unwrap();
        fs::write(
            fixture.path("log.key"),
            Keypair::from_seed([9; 32]).seed_hex(),
        )
        .unwrap();
        let body = TrustBody {
            v: 1,
            audience: AUDIENCE.into(),
            epoch: 1,
            policy_sha256: hex::encode(Sha256::digest(&bytes)),
            keys: BTreeMap::from([
                (
                    Role::World,
                    Keypair::from_seed(WORLD_SEED).verifying_key_hex(),
                ),
                (
                    Role::Fault,
                    Keypair::from_seed(FAULT_SEED).verifying_key_hex(),
                ),
                (Role::Vla, Keypair::from_seed(VLA_SEED).verifying_key_hex()),
            ]),
        };
        let bundle = sign_bundle(
            &serde_json::to_string(&body).unwrap(),
            &hex::encode(ROOT_SEED),
        )
        .unwrap();
        fs::write(
            fixture.path("trust.json"),
            serde_json::to_vec(&bundle).unwrap(),
        )
        .unwrap();
        fixture.reset_normal();
        fixture
    }

    fn path(&self, name: &str) -> PathBuf {
        self.dir.path().join(name)
    }

    fn reset_normal(&self) {
        let out = Command::new(env!("CARGO_BIN_EXE_haetae"))
            .args(["state", "set", "--state"])
            .arg(self.path("state.json"))
            .args([
                "--mode",
                "normal",
                "--by",
                "cli-test",
                "--reason",
                "fixture reset",
            ])
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    fn command(&self, log: &str, authenticated: bool, durable: bool, recorder: bool) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_haetae"));
        command
            .args(["enforce", "--stdio", "--policy"])
            .arg(self.path("policy.json"));
        if durable {
            command.arg("--state").arg(self.path("state.json"));
        } else {
            command.arg("--ephemeral");
        }
        if authenticated {
            command
                .arg("--trust")
                .arg(self.path("trust.json"))
                .arg("--root-pubkey")
                .arg(&self.root_pubkey);
        }
        if recorder {
            command
                .arg("--sillok")
                .arg(self.path(log))
                .arg("--key")
                .arg(self.path("log.key"));
        }
        command
    }

    fn spawn(&self, log: &str) -> Session {
        let mut child = self
            .command(log, true, true, true)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let output = BufReader::new(child.stdout.take().unwrap());
        Session {
            child,
            input: Some(input),
            output,
        }
    }
}

struct Session {
    child: Child,
    input: Option<ChildStdin>,
    output: BufReader<ChildStdout>,
}

impl Session {
    fn exchange(&mut self, t: u64, k: &str, data: &str) -> Value {
        let input = self.input.as_mut().unwrap();
        writeln!(input, "{}", json!({"t":t,"k":k,"data":data})).unwrap();
        input.flush().unwrap();
        let mut line = String::new();
        assert!(
            self.output.read_line(&mut line).unwrap() > 0,
            "gate closed without a Step"
        );
        serde_json::from_str(&line).unwrap()
    }

    fn send(&mut self, t: u64, role: Role, counter: u64, payload: Value, seed: [u8; 32]) -> Value {
        self.exchange(t, "signed", &signed(role, counter, payload, seed))
    }

    fn prepare(&mut self, w: Value) {
        let t = w["stamp_ms"].as_u64().unwrap();
        let answer = self.send(t, Role::World, 1, w, WORLD_SEED);
        assert!(answer["outcome"]["world_updated"].is_object(), "{answer}");
        self.send(t, Role::Vla, 1, stop(1, t), VLA_SEED);
    }

    fn close(&mut self) {
        self.input.take();
        assert!(
            !self.child.wait().unwrap().success(),
            "EOF must preserve unclean restart state"
        );
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn assert_no_execute(answer: &Value) {
    assert_eq!(
        answer["cmd"],
        json!({"linear":0.0,"angular":0.0}),
        "{answer}"
    );
    assert!(answer["arm"]["execute"].is_null(), "{answer}");
    assert!(answer["status"]["active"].is_null(), "{answer}");
}

fn assert_household_denied(answer: &Value, reason: &str) {
    assert_no_execute(answer);
    assert_eq!(answer["outcome"]["decision"]["verdict"], "bul", "{answer}");
    assert!(
        answer["outcome"]["decision"]["fired"]
            .as_array()
            .unwrap()
            .iter()
            .any(|fired| fired == reason),
        "{answer}"
    );
}

#[test]
fn household_policy_requires_signed_root_durable_state_and_recorder() {
    let fixture = Fixture::new();
    let household_error =
        "household protection requires signed trust, pinned root, durable state and recorder";
    for (authenticated, durable, recorder, expected_error) in [
        (
            false,
            true,
            true,
            "persistent enforcement requires --trust and --root-pubkey",
        ),
        (false, false, true, household_error),
        (true, false, true, household_error),
        (
            true,
            true,
            false,
            "persistent enforcement requires --sillok and --key",
        ),
    ] {
        let out = fixture
            .command("startup.jsonl", authenticated, durable, recorder)
            .output()
            .unwrap();
        assert!(!out.status.success());
        assert!(
            out.stdout.is_empty(),
            "no Step may be emitted on startup failure"
        );
        assert!(
            String::from_utf8_lossy(&out.stderr).contains(expected_error),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
}

#[test]
fn exact_safe_signed_trajectory_reaches_real_execute_output() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("safe.jsonl");
    session.prepare(world(1000));
    let proposal = trajectory(2, 1000, 0.1);
    let answer = session.send(1000, Role::Vla, 2, proposal.clone(), VLA_SEED);
    assert_eq!(answer["outcome"]["decision"]["verdict"], "yun", "{answer}");
    assert_eq!(
        answer["arm"],
        json!({"execute":{"points":proposal["action"]["points"]}}),
        "{answer}"
    );
    assert!(answer["stop"].is_null(), "{answer}");
    // Execute is emitted only after the accepted role counter is checkpointed.
    let state: Value =
        serde_json::from_slice(&fs::read(fixture.path("state.json")).unwrap()).unwrap();
    assert_eq!(state["auth_epoch"], 1);
    assert_eq!(state["counters"]["vla"], 2);
}

#[test]
fn signed_world_changes_cancel_an_already_admitted_household_trajectory() {
    for case in ["hazard", "missing", "stale", "identity"] {
        let fixture = Fixture::new();
        let mut session = fixture.spawn("active.jsonl");
        session.prepare(world(1000));
        let accepted = session.send(1000, Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED);
        assert!(accepted["arm"]["execute"].is_object(), "{accepted}");
        let mut changed = world(1050);
        changed["robot"]["joints"][0]["position"] = json!(0.05);
        changed["semantic"]["revision"] = json!(2);
        match case {
            "hazard" => {
                changed["semantic"]["regions"][0]["bounds"] = json!({
                    "min":{"x":5.18,"y":4.99,"z":-0.02},
                    "max":{"x":5.22,"y":5.03,"z":0.02}
                });
            }
            "missing" => {
                changed.as_object_mut().unwrap().remove("semantic");
            }
            "stale" => changed["semantic"]["observed_ms"] = json!(799),
            "identity" => changed["semantic"]["item_id"] = json!("different-item"),
            _ => unreachable!(),
        }
        let revoked = session.send(1050, Role::World, 2, changed, WORLD_SEED);
        assert_eq!(revoked["arm"], "cancel", "{case}: {revoked}");
        assert!(revoked["status"]["active"].is_null(), "{case}: {revoked}");
        assert_eq!(revoked["status"]["armed"], json!([]), "{case}: {revoked}");
        assert_no_execute(&revoked);
    }
}

#[test]
fn valid_vla_signature_cannot_omit_semantic_binding_or_world_facts() {
    for missing_world in [false, true] {
        let fixture = Fixture::new();
        let mut session = fixture.spawn("missing.jsonl");
        let mut w = world(1000);
        let mut p = trajectory(2, 1000, 0.1);
        if missing_world {
            w.as_object_mut().unwrap().remove("semantic");
        } else {
            p.as_object_mut().unwrap().remove("semantic");
        }
        session.prepare(w);
        let answer = session.send(1000, Role::Vla, 2, p, VLA_SEED);
        assert_household_denied(
            &answer,
            if missing_world {
                "household:missing-world"
            } else {
                "household:missing-binding"
            },
        );
    }
}

#[test]
fn every_bound_identity_and_revision_is_checked_for_signed_proposals() {
    let mutations = [
        ("schema_version", json!(2)),
        ("world_revision", json!(2)),
        ("task_revision", json!(2)),
        ("task_id", json!("other-task")),
        ("step_id", json!("other-step")),
        ("robot_id", json!("other-robot")),
        (
            "model_sha256",
            json!("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ),
        ("tool_id", json!("other-tool")),
        ("item_id", json!("other-item")),
    ];
    for (field, value) in mutations {
        let fixture = Fixture::new();
        let mut session = fixture.spawn("mismatch.jsonl");
        session.prepare(world(1000));
        let mut proposal = trajectory(2, 1000, 0.1);
        proposal["semantic"][field] = value;
        let answer = session.send(1000, Role::Vla, 2, proposal, VLA_SEED);
        assert_household_denied(&answer, "household:binding-mismatch");
    }
}

#[test]
fn freshly_signed_world_does_not_refresh_old_original_semantic_observation() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("stale.jsonl");
    let mut w = world(1000);
    w["semantic"]["observed_ms"] = json!(799);
    session.prepare(w);
    let answer = session.send(1000, Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED);
    assert_household_denied(&answer, "household:stale-world");
}

#[test]
fn altered_waypoints_are_rechecked_using_root_bound_fk() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("hazard.jsonl");
    session.prepare(world(1000));
    let safe = trajectory(2, 1000, 0.1);
    let hazardous = trajectory(2, 1000, -0.1);
    assert_eq!(safe["semantic"], hazardous["semantic"]);
    let answer = session.send(1000, Role::Vla, 2, hazardous, VLA_SEED);
    assert_household_denied(&answer, "household:human:protected-volume");
}

#[test]
fn household_mode_rejects_nonzero_base_motion_with_valid_context() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("base.jsonl");
    session.prepare(world(1000));
    let proposal = json!({"id":2,"source":"vla","timestamp_ms":1000,"semantic":binding(),
                          "action":{"type":"velocity","linear":0.1,"angular":0.0,"ttl_ms":200}});
    let answer = session.send(1000, Role::Vla, 2, proposal, VLA_SEED);
    assert_household_denied(&answer, "household:unsupported-action");
}

#[test]
fn vla_key_cannot_authenticate_or_inject_trusted_world_context() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("forgery.jsonl");
    session.prepare(world(1000));
    let wrong_key = session.send(1000, Role::World, 2, world(1000), VLA_SEED);
    assert_no_execute(&wrong_key);
    assert!(wrong_key["outcome"]["rejected"]["error"]
        .as_str()
        .unwrap()
        .contains("signature"));
    let wrong_role = session.send(1000, Role::Vla, 2, world(1000), VLA_SEED);
    assert_no_execute(&wrong_role);
    assert!(
        wrong_role["outcome"]["rejected"].is_object(),
        "{wrong_role}"
    );
    let mut scene_claim = trajectory(2, 1000, 0.1);
    scene_claim["semantic"]["item"] = json!("inert");
    let wrong_fields = session.send(1000, Role::Vla, 2, scene_claim, VLA_SEED);
    assert_no_execute(&wrong_fields);
    assert!(wrong_fields["outcome"]["rejected"]["error"]
        .as_str()
        .unwrap()
        .contains("unknown field"));
    // Forged inputs have neither overwritten the valid world nor consumed a
    // valid VLA counter. Explicit re-arm and the original safe scene still work.
    session.send(1000, Role::Vla, 2, stop(2, 1000), VLA_SEED);
    let answer = session.send(1000, Role::Vla, 3, trajectory(3, 1000, 0.1), VLA_SEED);
    assert!(answer["arm"]["execute"].is_object(), "{answer}");
}

#[test]
fn signed_stop_requires_neither_world_nor_semantic_context() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("stop.jsonl");
    let answer = session.send(1000, Role::Vla, 1, stop(1, 1000), VLA_SEED);
    assert_no_execute(&answer);
    assert_eq!(answer["outcome"]["decision"]["verdict"], "yun", "{answer}");
    assert_eq!(
        answer["outcome"]["decision"]["action"],
        json!({"type":"stop"})
    );
}

#[test]
fn accepted_household_signature_cannot_replay_after_restart_and_operator_reset() {
    let fixture = Fixture::new();
    let motion = signed(Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED);
    let mut session = fixture.spawn("first.jsonl");
    session.prepare(world(1000));
    assert!(session.exchange(1000, "signed", &motion)["arm"]["execute"].is_object());
    session.close();
    let state: Value =
        serde_json::from_slice(&fs::read(fixture.path("state.json")).unwrap()).unwrap();
    assert_eq!(state["running"], true);
    fixture.reset_normal();
    let mut restarted = fixture.spawn("second.jsonl");
    restarted.send(1100, Role::World, 2, world(1100), WORLD_SEED);
    restarted.send(1100, Role::Vla, 3, stop(3, 1100), VLA_SEED);
    let answer = restarted.exchange(1100, "signed", &motion);
    assert_no_execute(&answer);
    assert!(
        answer["outcome"]["rejected"]["error"]
            .as_str()
            .unwrap()
            .contains("replayed"),
        "{answer}"
    );
}

#[test]
fn root_signed_policy_hash_rejects_changed_household_policy_bytes() {
    let fixture = Fixture::new();
    // Whitespace leaves the parsed household configuration valid, while its
    // exact byte hash no longer agrees with the root-signed trust bundle.
    let mut bytes = fs::read(fixture.path("policy.json")).unwrap();
    bytes.push(b'\n');
    fs::write(fixture.path("policy.json"), bytes).unwrap();
    let out = fixture
        .command("tampered.jsonl", true, true, true)
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("policy hash does not match trust bundle"),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
}
