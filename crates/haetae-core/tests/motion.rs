use haetae_core::*;

const POLICY: &str = r#"{
  "allowed_sources":["vla"],
  "envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
  "base":{"footprint_radius":0.25,"max_decel":1.0,"max_angular":1.0,"latency_ms":50,"max_ttl_ms":200},
  "arm":{"joints":[{"name":"shoulder","min_position":-1.0,"max_position":1.0,"max_velocity":2.0,"max_acceleration":40.0}],"max_points":8,"max_duration_ms":200,"max_start_error":0.01,"max_tracking_error":0.05,"min_confidence":0.9},
  "zones":[{"id":"thin","area":{"min":{"x":2.0,"y":0.0},"max":{"x":2.01,"y":3.0}},"no_entry":true}]
}"#;

fn gate() -> Gate {
    Gate::new(Policy::from_json(POLICY).unwrap()).unwrap()
}

fn world() -> WorldSnapshot {
    WorldSnapshot {
        stamp_ms: 1000,
        robot: RobotState {
            pose: Point2::new(1.0, 1.0),
            yaw: Some(0.0),
            twist: Some(Twist2 {
                linear: 0.0,
                angular: 0.0,
            }),
            joints: Some(vec![JointSample {
                name: "shoulder".into(),
                position: 0.0,
                velocity: 0.0,
            }]),
            holding: None,
        },
        humans: Vec::new(),
        confidence: 1.0,
    }
}

fn proposal(action: ActionKind) -> ActionProposal {
    ActionProposal {
        id: 1,
        source: Source::Vla,
        timestamp_ms: 1000,
        action,
    }
}

#[test]
fn straight_disc_sweep_catches_a_thin_zone() {
    let d = gate().judge_at(
        &proposal(ActionKind::Velocity {
            linear: 0.8,
            angular: 0.0,
            ttl_ms: 200,
        }),
        &world(),
        1000,
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["zone:thin"]);
}

#[test]
fn curvature_is_preserved_when_speed_and_turn_are_clamped() {
    let mut w = world();
    w.robot.pose = Point2::new(5.0, 5.0);
    let d = gate().judge_at(
        &proposal(ActionKind::Velocity {
            linear: 2.0,
            angular: 2.0,
            ttl_ms: 200,
        }),
        &w,
        1000,
    );
    assert_eq!(d.verdict, Verdict::Jeol);
    match d.action.unwrap() {
        ActionKind::Velocity {
            linear, angular, ..
        } => assert_eq!(linear / angular, 1.0),
        other => panic!("{other:?}"),
    }
    assert_eq!(d.expires_ms, Some(1200));
}

#[test]
fn zero_twist_is_a_stop_even_without_world_or_source_permission() {
    let mut p = proposal(ActionKind::Velocity {
        linear: 0.0,
        angular: 0.0,
        ttl_ms: 0,
    });
    p.source = Source::Peer;
    let mut w = world();
    w.confidence = -1.0;
    let d = gate().judge_at(&p, &w, 999_999);
    assert_eq!(d.verdict, Verdict::Yun);
    assert_eq!(d.action, Some(ActionKind::Stop));
}

#[test]
fn arm_chunk_enforces_measured_start_limits_and_final_stop() {
    let good = vec![
        JointWaypoint {
            time_from_start_ms: 0,
            positions: vec![0.0],
        },
        JointWaypoint {
            time_from_start_ms: 100,
            positions: vec![0.1],
        },
        JointWaypoint {
            time_from_start_ms: 200,
            positions: vec![0.1],
        },
    ];
    let d = gate().judge_at(
        &proposal(ActionKind::JointTrajectory {
            points: good.clone(),
            ttl_ms: 200,
        }),
        &world(),
        1000,
    );
    assert_eq!(d.verdict, Verdict::Yun);
    let mut bad = good;
    bad[1].positions[0] = 2.0;
    let d = gate().judge_at(
        &proposal(ActionKind::JointTrajectory {
            points: bad,
            ttl_ms: 200,
        }),
        &world(),
        1000,
    );
    assert_eq!(d.fired, ["envelope:arm-position"]);
}

#[test]
fn arm_chunk_denies_missing_measured_state() {
    let mut w = world();
    w.robot.joints = None;
    let d = gate().judge_at(
        &proposal(ActionKind::JointTrajectory {
            points: vec![],
            ttl_ms: 200,
        }),
        &w,
        1000,
    );
    assert_eq!(d.fired, ["envelope:arm-duration"]);
}
