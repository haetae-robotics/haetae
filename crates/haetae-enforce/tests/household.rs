//! Actuator-facing checks for the protected household scope. Trusted semantic
//! observations are supplied by the world role; proposals carry only bindings.
use std::collections::BTreeMap;
use std::fs;

use ed25519_dalek::SigningKey;
use haetae_core::{ActionKind, ActionProposal, Policy, Source, WorldSnapshot};
use haetae_enforce::auth::{sign_bundle, sign_input, AuthVerifier, Role, SignedInput, TrustBody};
use haetae_enforce::{ArmOutput, Enforcer, EnforcerConfig, Step};
use haetae_runtime::Inbound;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn policy() -> Policy {
    let mut chain: Vec<Value> = (0..4)
        .map(|i| json!({"xyz":[0,0,0],"rpy":[0,0,0],"joint_index":i,"axis":"z"}))
        .collect();
    chain.push(json!({"xyz":[0.34,0,0],"rpy":[0,0,0],"joint_index":null,"axis":null}));
    serde_json::from_value(json!({
        "allowed_sources":["vla","planner"],
        "freshness":{"world_max_age_ms":200,"proposal_max_age_ms":1000,"future_tolerance_ms":20},
        "envelope":{"max_speed":1,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
        "arm":{
            "joints":(1..=4).map(|i| json!({"name":format!("joint{i}"),"min_position":-1,"max_position":1,"max_velocity":1,"max_acceleration":40})).collect::<Vec<_>>(),
            "max_points":8,"max_duration_ms":1000,"max_start_error":0.01,
            "max_tracking_error":0.05,"min_confidence":0.9
        },
        "household":{
            "schema_version":1,"robot_id":"test-robot","model_sha256":"a".repeat(64),
            "tool_id":"test-tool","chain":chain,"sample_step_rad":0.002,"swept_radius_m":0.15
        }
    })).unwrap()
}

fn semantic(t: u64) -> Value {
    json!({
        "schema_version":1,"revision":1,"task_revision":1,
        "task_id":"task-1","step_id":"step-1","robot_id":"test-robot",
        "model_sha256":"a".repeat(64),"tool_id":"test-tool","item_id":"item-1",
        "observed_ms":t,"confidence":1,"coverage_known":true,
        "item":"inert","regions":[],"base_pose":{"x":1,"y":1},"base_yaw":0
    })
}

fn world(t: u64, position: f64, facts: Option<Value>) -> Inbound {
    Inbound::World(serde_json::from_value::<WorldSnapshot>(json!({
        "stamp_ms":t,"robot":{
            "pose":{"x":1,"y":1},"yaw":0,"twist":{"linear":0,"angular":0},
            "joints":(1..=4).map(|i|json!({"name":format!("joint{i}"),"position":if i==1 {position} else {0.0},"velocity":0})).collect::<Vec<_>>(),
            "holding":null
        },"humans":[],"confidence":1,"semantic":facts
    })).unwrap())
}

fn binding() -> Value {
    json!({
        "schema_version":1,"world_revision":1,"task_revision":1,
        "task_id":"task-1","step_id":"step-1","robot_id":"test-robot",
        "model_sha256":"a".repeat(64),"tool_id":"test-tool","item_id":"item-1"
    })
}

fn proposal(id: u64, source: Source, binding: Option<Value>) -> ActionProposal {
    serde_json::from_value(json!({
        "id":id,"source":source,"timestamp_ms":1000,"semantic":binding,
        "action":{"type":"joint_trajectory","ttl_ms":1000,"points":[
            {"time_from_start_ms":0,"positions":[0,0,0,0]},
            {"time_from_start_ms":500,"positions":[0.25,0,0,0]},
            {"time_from_start_ms":1000,"positions":[0.25,0,0,0]}
        ]}
    }))
    .unwrap()
}

fn gate() -> Enforcer {
    Enforcer::open(policy(), EnforcerConfig::default(), 1000).unwrap()
}

fn arm(gate: &mut Enforcer, source: Source) {
    gate.handle(
        Inbound::Proposal(ActionProposal {
            id: 1,
            source,
            timestamp_ms: 1000,
            action: ActionKind::Stop,
            semantic: None,
        }),
        1000,
    );
}

fn accepted(gate: &mut Enforcer) {
    gate.handle(world(1000, 0.0, Some(semantic(1000))), 1000);
    arm(gate, Source::Vla);
    let p = proposal(2, Source::Vla, Some(binding()));
    let ActionKind::JointTrajectory {
        points: expected, ..
    } = p.action.clone()
    else {
        unreachable!()
    };
    let step = gate.handle(Inbound::Proposal(p), 1000);
    assert!(step.stop.is_none(), "{step:?}");
    let Some(ArmOutput::Execute { points }) = step.arm else {
        panic!("no exact actuator output")
    };
    assert_eq!(points, expected);
    assert!(step.status.active.is_some());
}

fn cancelled(step: Step) {
    assert!(matches!(step.arm, Some(ArmOutput::Cancel)), "{step:?}");
    assert!(step.stop.is_some());
    assert!(step.status.active.is_none());
    assert!(step.status.armed.is_empty());
    assert!(step.status.arm_cancelling);
    assert_eq!(step.cmd.linear, 0.0);
    assert_eq!(step.cmd.angular, 0.0);
}

#[test]
fn exact_safe_trajectory_executes_and_moving_joints_do_not_repeat_start_validation() {
    let mut g = gate();
    accepted(&mut g);
    // The original start is now 0.025 rad away, outside max_start_error.
    // Admission must not be repeated against moving measurements.
    let moved = g.handle(world(1050, 0.025, Some(semantic(1050))), 1050);
    assert!(moved.stop.is_none(), "{moved:?}");
    assert!(moved.arm.is_none());
    assert!(moved.status.active.is_some());
    let tick = g.tick(1090);
    assert!(tick.stop.is_none(), "{tick:?}");
    assert!(tick.status.active.is_some());
}

#[test]
fn every_allowed_proposal_source_needs_binding_and_trusted_facts() {
    for source in [Source::Vla, Source::Planner] {
        let mut g = gate();
        g.handle(world(1000, 0.0, Some(semantic(1000))), 1000);
        arm(&mut g, source);
        assert!(matches!(
            g.handle(
                Inbound::Proposal(proposal(2, source, Some(binding()))),
                1000
            )
            .arm,
            Some(ArmOutput::Execute { .. })
        ));
        for (facts, bound) in [(Some(semantic(1000)), None), (None, Some(binding()))] {
            let mut g = gate();
            g.handle(world(1000, 0.0, facts), 1000);
            arm(&mut g, source);
            let step = g.handle(Inbound::Proposal(proposal(2, source, bound)), 1000);
            assert!(step.arm.is_none(), "{step:?}");
            assert!(step.stop.is_some());
            assert!(step.status.active.is_none());
            assert!(step.status.armed.is_empty());
        }
    }
}

#[test]
fn the_binding_cannot_authorize_waypoints_whose_exact_fk_reaches_a_hazard() {
    let mut facts = semantic(1000);
    facts["item"] = json!("pressurized");
    facts["regions"] = json!([{
        "id":"heater","kind":"heat","state":"active",
        "bounds":{"min":{"x":1.28,"y":1.27,"z":-0.01},"max":{"x":1.36,"y":1.30,"z":0.01}},
        "contents":[],"contents_known":true
    }]);
    let mut g = gate();
    g.handle(world(1000, 0.0, Some(facts.clone())), 1000);
    arm(&mut g, Source::Vla);
    assert!(matches!(
        g.handle(
            Inbound::Proposal(proposal(2, Source::Vla, Some(binding()))),
            1000
        )
        .arm,
        Some(ArmOutput::Execute { .. })
    ));
    let mut g = gate();
    g.handle(world(1000, 0.0, Some(facts)), 1000);
    arm(&mut g, Source::Vla);
    let mut changed = proposal(2, Source::Vla, Some(binding()));
    if let ActionKind::JointTrajectory { points, .. } = &mut changed.action {
        points[1].positions[0] = 0.5;
        points[2].positions[0] = 0.5;
    }
    let rejected = g.handle(Inbound::Proposal(changed), 1000);
    assert!(rejected.arm.is_none(), "{rejected:?}");
    let Some(haetae_runtime::Outcome::Decision(decision)) = rejected.outcome else {
        panic!("expected semantic denial")
    };
    assert!(
        decision
            .fired
            .iter()
            .any(|f| f == "household:heat:hazardous-item"),
        "{decision:?}"
    );
}

#[test]
fn actual_base_motion_or_drift_cancels_even_if_semantic_anchor_is_unchanged() {
    for field in ["pose", "yaw", "twist"] {
        let mut g = gate();
        accepted(&mut g);
        let Inbound::World(mut observed) = world(1050, 0.025, Some(semantic(1050))) else {
            unreachable!()
        };
        match field {
            "pose" => observed.robot.pose.x += 0.01,
            "yaw" => observed.robot.yaw = Some(0.01),
            "twist" => observed.robot.twist.as_mut().unwrap().linear = 0.05,
            _ => unreachable!(),
        }
        cancelled(g.handle(Inbound::World(observed), 1050));
    }
}

#[test]
fn protected_scope_denies_base_motion_even_with_a_valid_semantic_binding() {
    let mut g = gate();
    g.handle(world(1000, 0.0, Some(semantic(1000))), 1000);
    arm(&mut g, Source::Vla);
    let mut p = proposal(2, Source::Vla, Some(binding()));
    p.action = ActionKind::Velocity {
        linear: 0.1,
        angular: 0.0,
        ttl_ms: 200,
    };
    let denied = g.handle(Inbound::Proposal(p), 1000);
    assert!(denied.stop.is_some());
    assert!(denied.arm.is_none());
    assert_eq!(denied.cmd.linear, 0.0);
    assert!(denied.status.active.is_none());
    assert!(denied.status.armed.is_empty());
}

#[test]
fn admission_denies_changed_binding_identities_versions_and_revisions() {
    for (field, value) in [
        ("schema_version", json!(2)),
        ("world_revision", json!(2)),
        ("task_revision", json!(2)),
        ("task_id", json!("other")),
        ("step_id", json!("other")),
        ("robot_id", json!("other")),
        ("model_sha256", json!("b".repeat(64))),
        ("tool_id", json!("other")),
        ("item_id", json!("other")),
    ] {
        let mut g = gate();
        g.handle(world(1000, 0.0, Some(semantic(1000))), 1000);
        arm(&mut g, Source::Vla);
        let mut bound = binding();
        bound[field] = value;
        let step = g.handle(
            Inbound::Proposal(proposal(2, Source::Vla, Some(bound))),
            1000,
        );
        assert!(step.arm.is_none(), "{field}: {step:?}");
        assert!(step.status.active.is_none());
        assert!(step.status.armed.is_empty());
    }
}

#[test]
fn lost_or_stale_semantic_context_cancels_live_trajectory() {
    let mut g = gate();
    accepted(&mut g);
    cancelled(g.handle(world(1050, 0.025, None), 1050));

    let mut g = gate();
    accepted(&mut g);
    // Motion perception is fresh. The independent semantic observation expires
    // first, while the original 1 s trajectory is still live.
    let fresh_motion = g.handle(world(1100, 0.05, Some(semantic(1000))), 1100);
    assert!(fresh_motion.stop.is_none(), "{fresh_motion:?}");
    cancelled(g.tick(1201));
}

#[test]
fn changed_identity_base_uncertainty_and_unknown_version_revoke_active_motion() {
    for (field, value) in [
        ("schema_version", json!(2)),
        ("task_revision", json!(2)),
        ("task_id", json!("other")),
        ("step_id", json!("other")),
        ("robot_id", json!("other")),
        ("model_sha256", json!("b".repeat(64))),
        ("tool_id", json!("other")),
        ("item_id", json!("other")),
        ("item", json!("unknown")),
        ("coverage_known", json!(false)),
        ("confidence", json!(0.5)),
        ("base_pose", json!({"x":1.1,"y":1})),
        ("base_yaw", json!(0.1)),
    ] {
        let mut g = gate();
        accepted(&mut g);
        let mut facts = semantic(1050);
        facts["revision"] = json!(2);
        facts[field] = value;
        cancelled(g.handle(world(1050, 0.025, Some(facts)), 1050));
    }
}

#[test]
fn newly_observed_hazard_revokes_but_safe_new_revision_keeps_motion() {
    let mut g = gate();
    accepted(&mut g);
    let mut safe = semantic(1050);
    safe["revision"] = json!(2);
    let continued = g.handle(world(1050, 0.025, Some(safe)), 1050);
    assert!(continued.stop.is_none(), "{continued:?}");
    assert!(continued.status.active.is_some());
    let mut danger = semantic(1100);
    danger["revision"] = json!(3);
    danger["regions"] = json!([{
        "id":"new-person","kind":"human","state":"active",
        "bounds":{"min":{"x":1.18,"y":0.98,"z":-0.02},"max":{"x":1.22,"y":1.02,"z":0.02}},
        "contents":[],"contents_known":true
    }]);
    cancelled(g.handle(world(1100, 0.05, Some(danger)), 1100));
}

#[test]
fn stop_is_available_without_semantic_facts_or_binding() {
    let mut g = gate();
    accepted(&mut g);
    let stopped = g.handle(
        Inbound::Proposal(ActionProposal {
            id: 3,
            source: Source::Vla,
            timestamp_ms: 1010,
            action: ActionKind::Stop,
            semantic: None,
        }),
        1010,
    );
    cancelled(stopped);
    let mut g = gate();
    g.handle(world(1000, 0.0, None), 1000);
    arm(&mut g, Source::Vla);
    assert!(g.tick(1000).status.armed.contains(&Source::Vla));
}

#[test]
fn a_valid_signature_cannot_skip_mandatory_semantic_checks() {
    let dir = tempfile::tempdir().unwrap();
    let root_seed = hex::encode([11u8; 32]);
    let proposal_seed = hex::encode([12u8; 32]);
    let pubkey = |seed| {
        hex::encode(
            SigningKey::from_bytes(&[seed; 32])
                .verifying_key()
                .to_bytes(),
        )
    };
    let policy_bytes = serde_json::to_vec(&policy()).unwrap();
    let trust = TrustBody {
        v: 1,
        audience: "test-robot".into(),
        epoch: 1,
        policy_sha256: hex::encode(Sha256::digest(&policy_bytes)),
        keys: BTreeMap::from([
            (Role::Vla, pubkey(12)),
            (Role::World, pubkey(13)),
            (Role::Fault, pubkey(14)),
        ]),
    };
    let bundle = sign_bundle(&serde_json::to_string(&trust).unwrap(), &root_seed).unwrap();
    let trust_path = dir.path().join("trust.json");
    fs::write(&trust_path, serde_json::to_vec(&bundle).unwrap()).unwrap();
    let mut verifier = AuthVerifier::load(&trust_path, &pubkey(11), &policy_bytes).unwrap();
    let mut g = gate();
    g.handle(world(1000, 0.0, Some(semantic(1000))), 1000);
    arm(&mut g, Source::Vla);
    let signed = sign_input(
        SignedInput {
            v: 1,
            audience: "test-robot".into(),
            epoch: 1,
            counter: 1,
            role: Role::Vla,
            payload: serde_json::to_string(&proposal(2, Source::Vla, None)).unwrap(),
            signature: String::new(),
        },
        &proposal_seed,
    )
    .unwrap();
    let authenticated = verifier
        .verify(&serde_json::to_vec(&signed).unwrap())
        .unwrap();
    let denied = g.handle(authenticated, 1000);
    assert!(denied.arm.is_none());
    assert!(denied.status.active.is_none());
    assert!(denied.status.armed.is_empty());
}
