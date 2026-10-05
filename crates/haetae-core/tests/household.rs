//! Mandatory checks use the exact candidate and trusted facts, including for
//! clients that previously submitted raw joint trajectories.
use haetae_core::hazard::{Bounds3, ItemKind, Point3, Region, RegionKind, State};
use haetae_core::{household, ActionKind, ActionProposal, Gate, Policy, Verdict, WorldSnapshot};
use serde_json::{json, Value};

fn policy_value() -> Value {
    let mut chain: Vec<Value> = (0..4)
        .map(|i| {
            json!({
                "xyz":[0,0,0],"rpy":[0,0,0],"joint_index":i,"axis":"z"
            })
        })
        .collect();
    chain.push(json!({"xyz":[0.2,0,0],"rpy":[0,0,0]}));
    json!({
        "allowed_sources":["vla","planner"],
        "envelope":{"max_speed":1,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
        "freshness":{"world_max_age_ms":200,"proposal_max_age_ms":1000,"future_tolerance_ms":20},
        "arm":{"joints":(1..=4).map(|i| json!({"name":format!("joint{i}"),"min_position":-1,"max_position":1,"max_velocity":1,"max_acceleration":40})).collect::<Vec<_>>(),
            "max_points":8,"max_duration_ms":1000,"max_start_error":0.01,"max_tracking_error":0.05,"min_confidence":0.9},
        "household":{"schema_version":1,"robot_id":"robot","model_sha256":"a".repeat(64),"tool_id":"tool",
            "chain":chain,"sample_step_rad":0.002,"swept_radius_m":0.15}
    })
}
fn policy() -> Policy {
    Policy::from_json(&policy_value().to_string()).unwrap()
}
fn world() -> WorldSnapshot {
    serde_json::from_value(json!({
        "stamp_ms":1000,"confidence":1,"humans":[],
        "robot":{"pose":{"x":5,"y":5},"yaw":0,"twist":{"linear":0,"angular":0},
            "joints":(1..=4).map(|i| json!({"name":format!("joint{i}"),"position":0,"velocity":0})).collect::<Vec<_>>()},
        "semantic":{"schema_version":1,"revision":1,"task_revision":1,"task_id":"task","step_id":"step",
            "robot_id":"robot","model_sha256":"a".repeat(64),"tool_id":"tool","item_id":"item",
            "observed_ms":1000,"confidence":1,"coverage_known":true,"item":"inert","regions":[],
            "base_pose":{"x":5,"y":5},"base_yaw":0}
    })).unwrap()
}
fn proposal_value() -> Value {
    json!({
        "id":1,"source":"vla","timestamp_ms":1000,
        "semantic":{"schema_version":1,"world_revision":1,"task_revision":1,"task_id":"task","step_id":"step",
            "robot_id":"robot","model_sha256":"a".repeat(64),"tool_id":"tool","item_id":"item"},
        "action":{"type":"joint_trajectory","ttl_ms":1000,"points":[
            {"time_from_start_ms":0,"positions":[0,0,0,0]},
            {"time_from_start_ms":500,"positions":[0.25,0,0,0]},
            {"time_from_start_ms":1000,"positions":[0.25,0,0,0]}
        ]}
    })
}
fn proposal() -> ActionProposal {
    serde_json::from_value(proposal_value()).unwrap()
}
fn human(x: f64, y: f64) -> Region {
    Region {
        id: "person".into(),
        kind: RegionKind::Human,
        state: State::Active,
        bounds: Bounds3 {
            min: Point3 {
                x: x - 0.002,
                y: y - 0.002,
                z: -0.002,
            },
            max: Point3 {
                x: x + 0.002,
                y: y + 0.002,
                z: 0.002,
            },
        },
        contents: vec![],
        contents_known: true,
    }
}

#[test]
fn exact_safe_control_passes_and_missing_or_wrong_references_deny() {
    let gate = Gate::new(policy()).unwrap();
    let p = proposal();
    let allowed = gate.judge_at(&p, &world(), 1000);
    assert_eq!(allowed.verdict, Verdict::Yun);
    assert_eq!(allowed.action, Some(p.action));
    for field in [
        "schema_version",
        "world_revision",
        "task_revision",
        "task_id",
        "step_id",
        "robot_id",
        "model_sha256",
        "tool_id",
        "item_id",
    ] {
        let mut v = proposal_value();
        v["semantic"][field] = if v["semantic"][field].is_number() {
            json!(2)
        } else {
            json!("wrong")
        };
        let p: ActionProposal = serde_json::from_value(v).unwrap();
        let d = gate.judge_at(&p, &world(), 1000);
        assert_eq!(d.verdict, Verdict::Bul, "{field}");
        assert_eq!(d.fired, ["household:binding-mismatch"]);
    }
    let mut p = proposal();
    p.semantic = None;
    assert_eq!(
        gate.judge_at(&p, &world(), 1000).fired,
        ["household:missing-binding"]
    );
}

#[test]
fn trusted_fk_checks_exact_candidate_not_cartesian_claims_or_waypoint_endpoints() {
    let gate = Gate::new(policy()).unwrap();
    let mut w = world();
    // Both waypoint endpoints have x<5.1995, outside the inflated box.
    // The middle of the joint arc reaches x=5.2 and enters that box.
    w.robot.joints.as_mut().unwrap()[0].position = -0.125;
    w.semantic.as_mut().unwrap().regions = vec![human(5.3515, 5.0)];
    let mut p = proposal();
    if let ActionKind::JointTrajectory { points, .. } = &mut p.action {
        points[0].positions[0] = -0.125;
        points[1].positions[0] = 0.125;
        points[2].positions[0] = 0.125;
    }
    let d = gate.judge_at(&p, &w, 1000);
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["household:human:protected-volume"]);
    let mut spoof = proposal_value();
    for (field, value) in [
        ("item", json!("inert")),
        ("regions", json!([])),
        ("path", json!([])),
        ("confidence", json!(1)),
        ("coverage_known", json!(true)),
    ] {
        spoof["semantic"][field] = value;
        assert!(
            serde_json::from_value::<ActionProposal>(spoof.clone()).is_err(),
            "{field}"
        );
        spoof["semantic"].as_object_mut().unwrap().remove(field);
    }
}

#[test]
fn semantic_confidence_threshold_is_inclusive_at_point_nine() {
    let gate = Gate::new(policy()).unwrap();
    for (confidence, expected) in [(0.899999, Verdict::Bul), (0.9, Verdict::Yun)] {
        let mut w = world();
        w.semantic.as_mut().unwrap().confidence = confidence;
        assert_eq!(gate.judge_at(&proposal(), &w, 1000).verdict, expected);
    }
}

#[test]
fn stale_unknown_and_missing_facts_or_stationary_base_violation_deny() {
    let gate = Gate::new(policy()).unwrap();
    for change in 0..8 {
        let mut w = world();
        match change {
            0 => w.semantic = None,
            1 => w.semantic.as_mut().unwrap().observed_ms = 799,
            2 => w.semantic.as_mut().unwrap().observed_ms = 1001,
            3 => w.semantic.as_mut().unwrap().item = ItemKind::Unknown,
            4 => w.semantic.as_mut().unwrap().coverage_known = false,
            5 => w.robot.twist.as_mut().unwrap().linear = 0.01,
            6 => w.robot.pose.x += 0.003,
            _ => w.robot.yaw = Some(0.003),
        }
        assert_eq!(
            gate.judge_at(&proposal(), &w, 1000).verdict,
            Verdict::Bul,
            "{change}"
        );
    }
    for action in [
        ActionKind::Velocity {
            linear: 0.1,
            angular: 0.0,
            ttl_ms: 100,
        },
        ActionKind::MoveTo {
            goal: haetae_core::Point2::new(5.1, 5.0),
            speed: 0.1,
        },
        ActionKind::Place {
            at: haetae_core::Point2::new(5.0, 5.0),
        },
    ] {
        let mut p = proposal();
        p.action = action;
        assert_eq!(
            gate.judge_at(&p, &world(), 1000).fired,
            ["household:unsupported-action"]
        );
    }
    let mut stop = proposal();
    stop.semantic = None;
    stop.action = ActionKind::Stop;
    let mut missing = world();
    missing.semantic = None;
    assert_eq!(gate.judge_at(&stop, &missing, 999999).verdict, Verdict::Yun);
}

#[test]
fn active_recheck_uses_remaining_path_and_new_hazards_revoke() {
    let p = proposal();
    let initial = world();
    let original = initial.semantic.clone().unwrap();
    let mut w = initial.clone();
    w.stamp_ms = 1400;
    w.robot.joints.as_mut().unwrap()[0].position = 0.2;
    let scene = w.semantic.as_mut().unwrap();
    scene.observed_ms = 1400;
    scene.revision = 2;
    assert!(household::recheck_active(&policy(), &p, &original, &w, 1000, 1400).is_ok());
    // A new person behind the already consumed path should not invalidate a
    // remaining path that stays clear; replaying the original path would deny.
    w.semantic.as_mut().unwrap().regions = vec![human(5.19, 4.848)];
    assert!(household::recheck_active(&policy(), &p, &original, &w, 1000, 1400).is_ok());
    w.semantic.as_mut().unwrap().regions = vec![human(5.19, 5.05)];
    assert_eq!(
        household::recheck_active(&policy(), &p, &original, &w, 1000, 1400).unwrap_err(),
        "household:human:protected-volume"
    );
    w.semantic.as_mut().unwrap().regions.clear();
    w.semantic.as_mut().unwrap().task_revision += 1;
    assert_eq!(
        household::recheck_active(&policy(), &p, &original, &w, 1000, 1400).unwrap_err(),
        "household:binding-mismatch"
    );
}

#[test]
fn malformed_fk_configuration_and_unbounded_work_are_denied() {
    for change in 0..8 {
        let mut v = policy_value();
        match change {
            0 => v["freshness"]["world_max_age_ms"] = json!(201),
            1 => v["household"]["chain"][1]["joint_index"] = json!(0),
            2 => v["household"]["chain"][0]["axis"] = json!("x"),
            3 => v["household"]["chain"][4]["xyz"] = json!([1, 0, 0]),
            4 => v["household"]["sample_step_rad"] = json!(0),
            5 => v["household"]["swept_radius_m"] = json!(0.01),
            6 => v["arm"]["max_tracking_error"] = json!(0.06),
            _ => v["household"]["model_sha256"] = json!("wrong"),
        }
        assert!(Policy::from_json(&v.to_string()).is_err(), "{change}");
    }
    let mut p = proposal();
    if let ActionKind::JointTrajectory { points, .. } = &mut p.action {
        *points = (0..8)
            .map(|i| haetae_core::JointWaypoint {
                time_from_start_ms: i * 100,
                positions: vec![if i % 2 == 0 { -1.0 } else { 1.0 }, 0.0, 0.0, 0.0],
            })
            .collect();
    }
    assert_eq!(
        household::check_proposal(&policy(), &p, &world(), 1000).unwrap_err(),
        "household:path-budget"
    );
}

#[test]
fn legacy_json_remains_byte_shape_compatible_without_new_optional_fields() {
    let p: ActionProposal = serde_json::from_value(
        json!({"id":1,"source":"vla","timestamp_ms":1000,"action":{"type":"stop"}}),
    )
    .unwrap();
    assert!(serde_json::to_value(p).unwrap().get("semantic").is_none());
    let mut w = world();
    w.semantic = None;
    assert!(serde_json::to_value(w).unwrap().get("semantic").is_none());
    let mut v = policy_value();
    v.as_object_mut().unwrap().remove("household");
    let legacy: Policy = serde_json::from_value(v).unwrap();
    assert!(serde_json::to_value(legacy)
        .unwrap()
        .get("household")
        .is_none());
}
