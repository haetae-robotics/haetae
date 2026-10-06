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
    for case in ["safe", "hazard", "missing", "stale", "identity"] {
        let fixture = Fixture::new();
        let mut session = fixture.spawn("active.jsonl");
        session.prepare(world(1000));
        let accepted = session.send(1000, Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED);
        assert!(accepted["arm"]["execute"].is_object(), "{accepted}");
        let mut changed = world(1050);
        changed["robot"]["joints"][0]["position"] = json!(0.05);
        changed["semantic"]["revision"] = json!(2);
        match case {
            "safe" => (),
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
        if case == "safe" {
            assert!(revoked["stop"].is_null(), "{revoked}");
            assert!(revoked["arm"].is_null(), "{revoked}");
            assert!(revoked["status"]["active"].is_object(), "{revoked}");
            assert_eq!(revoked["status"]["armed"], json!(["vla"]), "{revoked}");
            continue;
        }
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

fn state_file(fixture: &Fixture) -> Value {
    serde_json::from_slice(&fs::read(fixture.path("state.json")).unwrap()).unwrap()
}

fn measured_world(t: u64, position: f64) -> Value {
    let mut observed = world(t);
    observed["robot"]["joints"][0]["position"] = json!(position);
    observed
}

fn finish_motion(session: &mut Session, counter: &mut u64) {
    for t in [1100, 1200, 1250, 1300, 1350] {
        *counter += 1;
        let answer = session.send(t, Role::World, *counter, measured_world(t, 0.1), WORLD_SEED);
        if t == 1350 {
            assert_eq!(
                answer["status"]["history"]["motion_pending"], false,
                "{answer}"
            );
            assert_eq!(answer["status"]["history"]["material_effects_committed"], 0);
        }
    }
}

fn history_rejected(answer: &Value, reason: &str) {
    assert_no_execute(answer);
    assert_eq!(answer["outcome"]["rejected"]["error"], reason, "{answer}");
}

#[test]
fn reservation_and_exact_emitted_digest_are_durable_before_execute() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("reservation.jsonl");
    session.prepare(world(1000));
    let proposal = trajectory(2, 1000, 0.1);
    let accepted = session.send(1000, Role::Vla, 2, proposal.clone(), VLA_SEED);
    assert!(accepted["arm"]["execute"].is_object());
    let persisted = state_file(&fixture);
    assert_eq!(persisted["v"], 2);
    assert_eq!(persisted["counters"]["vla"], 2);
    let record = &persisted["history"]["steps"]["test-task/step-1"];
    assert_eq!(record["outcome"], "reserved");
    let typed: haetae_core::ActionProposal = serde_json::from_value(proposal).unwrap();
    assert_eq!(
        record["command_sha256"],
        hex::encode(Sha256::digest(serde_json::to_vec(&typed).unwrap()))
    );
}

#[test]
fn settled_motion_is_not_a_material_effect_and_a_fresh_counter_cannot_repeat_the_step() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("settled.jsonl");
    session.prepare(world(1000));
    assert!(
        session.send(1000, Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED)["arm"]["execute"]
            .is_object()
    );
    let mut counter = 1;
    finish_motion(&mut session, &mut counter);
    let persisted = state_file(&fixture);
    assert_eq!(
        persisted["history"]["steps"]["test-task/step-1"]["outcome"],
        "motion_settled"
    );
    session.send(1400, Role::Vla, 3, stop(3, 1400), VLA_SEED);
    let mut repeated = trajectory(4, 1400, 0.2);
    repeated["action"]["points"][0]["positions"][0] = json!(0.1);
    let answer = session.send(1400, Role::Vla, 4, repeated, VLA_SEED);
    history_rejected(&answer, "history:consumed-step");
}

#[test]
fn crash_and_mode_reset_preserve_pending_motion_and_require_stop_evidence_and_new_task_epoch() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("before-crash.jsonl");
    session.prepare(world(1000));
    assert!(
        session.send(1000, Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED)["arm"]["execute"]
            .is_object()
    );
    session.child.kill().unwrap();
    session.child.wait().unwrap();
    let before = state_file(&fixture)["history"].clone();
    fixture.reset_normal();
    assert_eq!(state_file(&fixture)["history"], before);
    let mut restored = fixture.spawn("after-crash.jsonl");
    let first = restored.send(1100, Role::World, 2, world(1100), WORLD_SEED);
    assert_eq!(first["status"]["history"]["motion_pending"], true);
    restored.send(1125, Role::World, 3, world(1125), WORLD_SEED);
    restored.send(1125, Role::Vla, 3, stop(3, 1125), VLA_SEED);
    history_rejected(
        &restored.send(1125, Role::Vla, 4, trajectory(4, 1125, 0.1), VLA_SEED),
        "history:unresolved-motion",
    );
    // Two new, trusted, measured stopped worlds resolve only the kinematic lock.
    restored.send(1150, Role::World, 4, world(1150), WORLD_SEED);
    restored.send(1200, Role::World, 5, world(1200), WORLD_SEED);
    restored.send(1200, Role::Vla, 5, stop(5, 1200), VLA_SEED);
    history_rejected(
        &restored.send(1200, Role::Vla, 6, trajectory(6, 1200, 0.1), VLA_SEED),
        "history:interrupted-task-revision",
    );
    let mut next = world(1250);
    next["semantic"]["revision"] = json!(2);
    next["semantic"]["task_revision"] = json!(2);
    next["semantic"]["step_id"] = json!("new-step");
    restored.send(1250, Role::World, 6, next.clone(), WORLD_SEED);
    restored.send(1250, Role::Vla, 7, stop(7, 1250), VLA_SEED);
    let mut proposal = trajectory(8, 1250, 0.1);
    proposal["semantic"]["world_revision"] = json!(2);
    proposal["semantic"]["task_revision"] = json!(2);
    proposal["semantic"]["step_id"] = json!("new-step");
    let accepted = restored.send(1250, Role::Vla, 8, proposal, VLA_SEED);
    assert!(accepted["arm"]["execute"].is_object(), "{accepted}");
    assert_eq!(
        accepted["status"]["history"]["material_effects_committed"],
        0
    );
}

#[test]
fn remembered_bleach_survives_crash_empty_claim_and_reset_and_blocks_ammonia() {
    for initial_observed_ms in [1000, 750, 1001] {
        let fixture = Fixture::new();
        let mut observed = world(1000);
        observed["semantic"]["observed_ms"] = json!(initial_observed_ms);
        observed["semantic"]["regions"] = json!([{
            "id":"vessel-1","kind":"container","state":"active",
            "bounds":{"min":{"x":5.18,"y":4.99,"z":-0.02},"max":{"x":5.22,"y":5.03,"z":0.02}},
            "contents":["bleach"],"contents_known":true
        }]);
        let mut first = fixture.spawn("facts-before-crash.jsonl");
        let world_counter_offset = if initial_observed_ms > 1000 {
            // The outer stamp is within the policy's 20ms future tolerance.
            // A semantic fact must still not receive a durable ACK before its
            // original observation time. Retry it after that time arrives.
            observed["stamp_ms"] = json!(initial_observed_ms);
            history_rejected(
                &first.send(1000, Role::World, 1, observed.clone(), WORLD_SEED),
                "history:future-observation",
            );
            assert!(state_file(&fixture)["history"]["floor"].is_null());
            assert_eq!(state_file(&fixture)["history"]["containers"], json!({}));
            let acknowledged = first.send(
                initial_observed_ms,
                Role::World,
                2,
                observed.clone(),
                WORLD_SEED,
            );
            assert!(acknowledged["outcome"]["world_updated"].is_object());
            first.send(
                initial_observed_ms,
                Role::Vla,
                1,
                stop(1, initial_observed_ms),
                VLA_SEED,
            );
            1
        } else {
            first.prepare(observed.clone());
            0
        };
        let counter_offset = if initial_observed_ms < 1000 {
            assert_household_denied(
                &first.send(1000, Role::Vla, 2, trajectory(2, 1000, 0.1), VLA_SEED),
                "household:stale-world",
            );
            1
        } else {
            0
        };
        // An acknowledged non-cancelling world has its facts durably committed.
        assert_eq!(
            state_file(&fixture)["history"]["containers"]["vessel-1"]["contents"],
            json!(["bleach"])
        );
        first.child.kill().unwrap();
        first.child.wait().unwrap();
        fixture.reset_normal();
        let mut next = fixture.spawn("facts-after-crash.jsonl");
        observed["stamp_ms"] = json!(1100);
        observed["semantic"]["observed_ms"] = json!(1100);
        observed["semantic"]["revision"] = json!(2);
        observed["semantic"]["task_revision"] = json!(2);
        observed["semantic"]["item_id"] = json!("ammonia-object");
        observed["semantic"]["item"] = json!("ammonia");
        observed["semantic"]["regions"][0]["contents"] = json!([]);
        next.send(
            1100,
            Role::World,
            2 + world_counter_offset,
            observed.clone(),
            WORLD_SEED,
        );
        next.send(1100, Role::Vla, 2 + counter_offset, stop(2, 1100), VLA_SEED);
        let mut proposal = trajectory(3, 1100, 0.1);
        for (field, value) in [
            ("world_revision", json!(2)),
            ("task_revision", json!(2)),
            ("item_id", json!("ammonia-object")),
        ] {
            proposal["semantic"][field] = value;
        }
        assert_household_denied(
            &next.send(1100, Role::Vla, 3 + counter_offset, proposal, VLA_SEED),
            "household:chemicals:incompatible",
        );
        // Positive non-touching route: same material and retained contaminant.
        observed["stamp_ms"] = json!(1150);
        observed["semantic"]["observed_ms"] = json!(1150);
        observed["semantic"]["revision"] = json!(3);
        observed["semantic"]["regions"][0]["bounds"] =
            json!({"min":{"x":7,"y":7,"z":0},"max":{"x":7.1,"y":7.1,"z":0.1}});
        next.send(
            1150,
            Role::World,
            3 + world_counter_offset,
            observed,
            WORLD_SEED,
        );
        next.send(1150, Role::Vla, 4 + counter_offset, stop(4, 1150), VLA_SEED);
        let mut safe = trajectory(5, 1150, 0.1);
        safe["semantic"]["world_revision"] = json!(3);
        safe["semantic"]["task_revision"] = json!(2);
        safe["semantic"]["item_id"] = json!("ammonia-object");
        assert!(
            next.send(1150, Role::Vla, 5 + counter_offset, safe, VLA_SEED)["arm"]["execute"]
                .is_object()
        );
    }
}

#[test]
fn durable_floor_rejects_rollback_and_changed_equal_revision_without_restoring_world_authority() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("floor-before.jsonl");
    let mut observed = world(1000);
    observed["semantic"]["revision"] = json!(10);
    observed["semantic"]["task_revision"] = json!(5);
    session.prepare(observed.clone());
    session.child.kill().unwrap();
    session.child.wait().unwrap();
    fixture.reset_normal();
    let mut restored = fixture.spawn("floor-after.jsonl");
    let no_world = restored.exchange(1050, "tick", "");
    assert_eq!(no_world["stop"], "no_world");
    observed["stamp_ms"] = json!(1100);
    observed["semantic"]["observed_ms"] = json!(1100);
    let mut rollback = observed.clone();
    rollback["semantic"]["revision"] = json!(9);
    history_rejected(
        &restored.send(1100, Role::World, 2, rollback, WORLD_SEED),
        "history:observation-rollback",
    );
    let mut altered = observed.clone();
    altered["semantic"]["item"] = json!("pressurized");
    history_rejected(
        &restored.send(1100, Role::World, 3, altered, WORLD_SEED),
        "history:same-revision-changed-facts",
    );
    let accepted = restored.send(1100, Role::World, 4, observed, WORLD_SEED);
    assert!(
        accepted["outcome"]["world_updated"].is_object(),
        "{accepted}"
    );
    assert!(accepted["status"]["active"].is_null());
}

#[test]
fn future_semantic_stamp_cannot_poison_durable_floor_or_restart() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("future-floor-before.jsonl");
    session.prepare(world(1000));
    let mut future = world(1100);
    future["semantic"]["observed_ms"] = json!(u64::MAX);
    future["semantic"]["revision"] = json!(2);
    history_rejected(
        &session.send(1100, Role::World, 2, future, WORLD_SEED),
        "history:future-observation",
    );
    assert_eq!(
        state_file(&fixture)["history"]["floor"]["observed_ms"],
        1000
    );
    assert_eq!(state_file(&fixture)["history"]["floor"]["revision"], 1);
    session.close();
    fixture.reset_normal();
    let mut restored = fixture.spawn("future-floor-after.jsonl");
    let fresh = restored.send(1100, Role::World, 3, world(1100), WORLD_SEED);
    assert!(fresh["outcome"]["world_updated"].is_object(), "{fresh}");
    assert_eq!(
        state_file(&fixture)["history"]["floor"]["observed_ms"],
        1100
    );
    restored.send(1100, Role::Vla, 2, stop(2, 1100), VLA_SEED);
    let positive = restored.send(1100, Role::Vla, 3, trajectory(3, 1100, 0.1), VLA_SEED);
    assert!(positive["arm"]["execute"].is_object(), "{positive}");
}

#[test]
fn corrupted_protected_history_fails_startup_and_is_not_overwritten() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("corrupt-before.jsonl");
    session.prepare(world(1000));
    session.child.kill().unwrap();
    session.child.wait().unwrap();
    let corrupt = b"{\"v\":2,\"history\":broken";
    fs::write(fixture.path("state.json"), corrupt).unwrap();
    let out = fixture
        .command("corrupt-after.jsonl", true, true, true)
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
    assert_eq!(fs::read(fixture.path("state.json")).unwrap(), corrupt);
}

#[test]
fn failed_atomic_reservation_checkpoint_emits_no_positive_step() {
    let fixture = Fixture::new();
    let mut session = fixture.spawn("commit-failure.jsonl");
    session.prepare(world(1000));
    session.send(1000, Role::World, 2, world(1000), WORLD_SEED);
    fs::rename(fixture.path("state.json"), fixture.path("saved-state.json")).unwrap();
    fs::create_dir(fixture.path("state.json")).unwrap();
    let input = session.input.as_mut().unwrap();
    writeln!(
        input,
        "{}",
        json!({"t":1000,"k":"signed","data":signed(Role::Vla,2,trajectory(2,1000,0.1),VLA_SEED)})
    )
    .unwrap();
    input.flush().unwrap();
    let mut output = String::new();
    assert_eq!(
        session.output.read_line(&mut output).unwrap(),
        0,
        "{output}"
    );
    assert!(!session.child.wait().unwrap().success());
    let saved: Value =
        serde_json::from_slice(&fs::read(fixture.path("saved-state.json")).unwrap()).unwrap();
    assert!(saved["history"]["steps"].as_object().unwrap().is_empty());
    assert_eq!(saved["counters"]["vla"], 1);
}
