use haetae_core::{
    ActionKind, ActionProposal, Human, HumanClass, JointSample, JointWaypoint, Mode, Point2,
    Policy, RobotState, Source, WorldSnapshot,
};
use haetae_enforce::{fault, ArmOutput, Enforcer, EnforcerConfig, StopReason};
use haetae_runtime::{Inbound, RuntimeConfig};

fn policy() -> Policy {
    Policy::from_json(r#"{
      "allowed_sources":["vla"],
      "freshness":{"world_max_age_ms":200,"proposal_max_age_ms":1000,"future_tolerance_ms":20},
      "envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
      "arm":{"joints":[{"name":"shoulder","min_position":-1,"max_position":1,"max_velocity":2,"max_acceleration":40}],"max_points":8,"max_duration_ms":200,"max_start_error":0.01,"max_tracking_error":0.05,"min_confidence":0.9}
    }"#).unwrap()
}

fn world(t: u64, position: f64, velocity: f64) -> WorldSnapshot {
    WorldSnapshot {
        stamp_ms: t,
        robot: RobotState {
            pose: Point2::new(5.0, 5.0),
            yaw: None,
            twist: None,
            joints: Some(vec![JointSample {
                name: "shoulder".into(),
                position,
                velocity,
            }]),
            holding: None,
        },
        humans: vec![],
        confidence: 1.0,
        semantic: None,
    }
}

fn proposal(id: u64, t: u64, action: ActionKind) -> Inbound {
    Inbound::Proposal(ActionProposal {
        id,
        source: Source::Vla,
        timestamp_ms: t,
        action,
        semantic: None,
    })
}

fn chunk(id: u64, t: u64, start: f64) -> Inbound {
    proposal(
        id,
        t,
        ActionKind::JointTrajectory {
            ttl_ms: 200,
            points: vec![
                JointWaypoint {
                    time_from_start_ms: 0,
                    positions: vec![start],
                },
                JointWaypoint {
                    time_from_start_ms: 100,
                    positions: vec![start + 0.1],
                },
                JointWaypoint {
                    time_from_start_ms: 200,
                    positions: vec![start + 0.1],
                },
            ],
        },
    )
}

#[test]
fn arm_chunk_requires_rearm_and_cancels_on_tracking_divergence() {
    let mut g = Enforcer::open(
        policy(),
        EnforcerConfig {
            runtime: RuntimeConfig::default(),
            state_path: None,
            tick_ms: 50,
        },
        1000,
    )
    .unwrap();
    g.handle(Inbound::World(world(1000, 0.0, 0.0)), 1000);
    assert!(g.handle(chunk(1, 1000, 0.0), 1000).arm.is_none());
    g.handle(proposal(2, 1000, ActionKind::Stop), 1000);
    let accepted = g.handle(chunk(3, 1000, 0.0), 1000);
    assert!(matches!(accepted.arm, Some(ArmOutput::Execute { .. })));
    assert!(accepted.stop.is_none());
    assert_eq!(accepted.status.active_expires_ms, Some(1200));
    let changed = g.handle(Inbound::World(world(1050, 0.2, 0.0)), 1050);
    assert!(matches!(changed.arm, Some(ArmOutput::Cancel)));
    assert_eq!(changed.stop, Some(StopReason::ArmSettling));
    assert_eq!(changed.cmd.linear, 0.0);
    g.handle(Inbound::World(world(1100, 0.2, 0.0)), 1100);
    g.handle(Inbound::World(world(1150, 0.2, 0.0)), 1150);
    assert_eq!(
        g.handle(chunk(4, 1150, 0.2), 1150).stop,
        Some(StopReason::Unarmed)
    );
    g.handle(proposal(5, 1150, ActionKind::Stop), 1150);
    assert!(matches!(
        g.handle(chunk(6, 1150, 0.2), 1150).arm,
        Some(ArmOutput::Execute { .. })
    ));
}

#[test]
fn arm_is_denied_if_a_human_is_present() {
    let mut g = Enforcer::open(policy(), EnforcerConfig::default(), 1000).unwrap();
    let mut w = world(1000, 0.0, 0.0);
    w.humans.push(Human {
        id: "person".into(),
        class: HumanClass::Adult,
        pos: Point2::new(9.0, 9.0),
    });
    g.handle(Inbound::World(w), 1000);
    g.handle(proposal(1, 1000, ActionKind::Stop), 1000);
    let denied = g.handle(chunk(2, 1000, 0.0), 1000);
    assert_eq!(denied.stop, Some(StopReason::Denied));
    assert!(denied.arm.is_none());
}

#[test]
fn delayed_pre_cancel_measurements_cannot_finish_settling() {
    let mut g = Enforcer::open(policy(), EnforcerConfig::default(), 1000).unwrap();
    g.handle(Inbound::World(world(1000, 0.0, 0.0)), 1000);
    g.handle(proposal(1, 1000, ActionKind::Stop), 1000);
    assert!(matches!(
        g.handle(chunk(2, 1000, 0.0), 1000).arm,
        Some(ArmOutput::Execute { .. })
    ));
    assert!(matches!(
        g.reject("bad input".into(), b"bad", 1050).arm,
        Some(ArmOutput::Cancel)
    ));
    for (stamp, arrival) in [(1025, 1051), (1040, 1052)] {
        assert_eq!(
            g.handle(Inbound::World(world(stamp, 0.0, 0.0)), arrival)
                .stop,
            Some(StopReason::ArmSettling)
        );
    }
    assert_eq!(
        g.handle(Inbound::World(world(1055, 0.0, 0.0)), 1055).stop,
        Some(StopReason::ArmSettling)
    );
    assert_eq!(
        g.handle(Inbound::World(world(1060, 0.0, 0.0)), 1060).stop,
        Some(StopReason::NoCommand)
    );
    assert_eq!(
        g.handle(chunk(3, 1060, 0.0), 1060).stop,
        Some(StopReason::Unarmed)
    );
}

#[test]
fn caution_denies_new_arm_chunks_and_cancels_active_chunks() {
    let mut g = Enforcer::open(policy(), EnforcerConfig::default(), 1000).unwrap();
    g.handle(Inbound::World(world(1000, 0.0, 0.0)), 1000);
    g.handle(proposal(1, 1000, ActionKind::Stop), 1000);
    assert!(matches!(
        g.handle(chunk(2, 1000, 0.0), 1000).arm,
        Some(ArmOutput::Execute { .. })
    ));
    let step = g.handle(fault("sensor", Mode::Caution, 1010), 1010);
    assert!(matches!(step.arm, Some(ArmOutput::Cancel)));
    assert_eq!(step.status.mode, Mode::Caution);
    let mut g = Enforcer::open(policy(), EnforcerConfig::default(), 1000).unwrap();
    g.handle(Inbound::World(world(1000, 0.0, 0.0)), 1000);
    g.handle(proposal(1, 1000, ActionKind::Stop), 1000);
    g.handle(fault("sensor", Mode::Caution, 1000), 1000);
    assert!(g.handle(chunk(2, 1000, 0.0), 1000).arm.is_none());
}
