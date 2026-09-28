use haetae_core::{
    ActionKind, ActionProposal, Human, HumanClass, Mode, Point2, Policy, RobotState, Source,
    WorldSnapshot,
};
use haetae_enforce::{fault, set_state, Enforcer, EnforcerConfig, StopReason};
use haetae_runtime::{Inbound, RuntimeConfig};

fn policy() -> Policy {
    Policy::from_json(r#"{
      "allowed_sources":["vla"],
      "freshness":{"world_max_age_ms":200,"proposal_max_age_ms":1000,"future_tolerance_ms":20},
      "envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}},
      "base":{"footprint_radius":0.25,"max_decel":1.0,"max_angular":1.0,"latency_ms":50,"max_ttl_ms":200},
      "rules":[{"id":"person","when":{"human_within":{"distance":0.3}},"then":"bul"}]
    }"#).unwrap()
}

fn world(t: u64) -> WorldSnapshot {
    WorldSnapshot {
        stamp_ms: t,
        robot: RobotState {
            pose: Point2::new(5.0, 5.0),
            yaw: Some(0.0),
            twist: None,
            joints: None,
            holding: None,
        },
        humans: vec![],
        confidence: 1.0,
    }
}

fn twist(id: u64, t: u64, linear: f64) -> Inbound {
    Inbound::Proposal(ActionProposal {
        id,
        source: Source::Vla,
        timestamp_ms: t,
        action: ActionKind::Velocity {
            linear,
            angular: 0.0,
            ttl_ms: 200,
        },
    })
}

fn gate() -> Enforcer {
    let cfg = EnforcerConfig {
        runtime: RuntimeConfig::default(),
        state_path: None,
        tick_ms: 50,
    };
    Enforcer::open(policy(), cfg, 1000).unwrap()
}

#[test]
fn startup_rearm_and_ttl() {
    let mut g = gate();
    assert_eq!(g.tick(1000).cmd.linear, 0.0);
    g.handle(Inbound::World(world(1000)), 1000);
    let suppressed = g.handle(twist(1, 1000, 0.5), 1000);
    assert_eq!(suppressed.stop, Some(StopReason::Unarmed));
    g.handle(twist(2, 1000, 0.0), 1000);
    let moving = g.handle(twist(3, 1000, 0.5), 1000);
    assert_eq!(moving.cmd.linear, 0.5);
    assert_eq!(g.tick(1199).cmd.linear, 0.5);
    let expired = g.tick(1200);
    assert_eq!(expired.stop, Some(StopReason::Expired));
    assert_eq!(expired.cmd.linear, 0.0);
}

#[test]
fn new_person_revokes_and_requires_a_new_zero() {
    let mut g = gate();
    g.handle(Inbound::World(world(1000)), 1000);
    g.handle(twist(1, 1000, 0.0), 1000);
    assert_eq!(g.handle(twist(2, 1000, 0.5), 1000).cmd.linear, 0.5);
    let mut blocked = world(1050);
    blocked.humans.push(Human {
        id: "child".into(),
        class: HumanClass::Child,
        pos: Point2::new(5.5, 5.0),
    });
    let stopped = g.handle(Inbound::World(blocked), 1050);
    assert_eq!(stopped.stop, Some(StopReason::Revoked));
    assert!(stopped.publish_now);
    assert_eq!(stopped.cmd.linear, 0.0);
    g.handle(Inbound::World(world(1100)), 1100);
    assert_eq!(
        g.handle(twist(3, 1100, 0.5), 1100).stop,
        Some(StopReason::Unarmed)
    );
}

#[test]
fn stale_world_latches_disarm_even_after_recovery() {
    let mut g = gate();
    g.handle(Inbound::World(world(1000)), 1000);
    g.handle(twist(1, 1000, 0.0), 1000);
    g.handle(twist(2, 1000, 0.5), 1000);
    let stopped = g.tick(1201);
    assert_eq!(stopped.stop, Some(StopReason::StaleWorld));
    g.handle(Inbound::World(world(1201)), 1201);
    assert_eq!(
        g.handle(twist(3, 1201, 0.5), 1201).stop,
        Some(StopReason::Unarmed)
    );
}

#[test]
fn malformed_input_and_hold_both_stop_motion() {
    let mut g = gate();
    g.handle(Inbound::World(world(1000)), 1000);
    g.handle(twist(1, 1000, 0.0), 1000);
    g.handle(twist(2, 1000, 0.5), 1000);
    assert_eq!(g.handle_bytes(b"garbage", 1010).cmd.linear, 0.0);
    g.handle(twist(3, 1010, 0.0), 1010);
    g.handle(twist(4, 1010, 0.5), 1010);
    let step = g.handle(fault("sensor", Mode::Hold, 1020), 1020);
    assert_eq!(step.stop, Some(StopReason::Mode(Mode::Hold)));
    assert_eq!(step.cmd.linear, 0.0);
}

#[test]
fn rejected_world_stops_an_active_command_immediately() {
    let mut g = gate();
    g.handle(Inbound::World(world(1000)), 1000);
    g.handle(twist(1, 1000, 0.0), 1000);
    assert_eq!(g.handle(twist(2, 1000, 0.5), 1000).cmd.linear, 0.5);
    let mut invalid = world(1010);
    invalid.confidence = 2.0;
    let stopped = g.handle(Inbound::World(invalid), 1010);
    assert_eq!(stopped.cmd.linear, 0.0);
    assert!(stopped.publish_now);
    assert_eq!(
        g.handle(twist(3, 1010, 0.5), 1010).stop,
        Some(StopReason::Unarmed)
    );
}

#[test]
fn unclean_restart_and_lock_fail_closed() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.json");
    // Explicit offline operator action is needed even on first boot.
    let cfg = || EnforcerConfig {
        runtime: RuntimeConfig::default(),
        state_path: Some(path.clone()),
        tick_ms: 50,
    };
    let first = Enforcer::open(policy(), cfg(), 1000).unwrap();
    assert_eq!(first.tick_ms(), 50);
    assert!(set_state(&path, Mode::Normal, "operator", "arm", 1001).is_err());
    drop(first); // crash: running marker remains
    let mut reopened = Enforcer::open(policy(), cfg(), 1100).unwrap();
    assert_eq!(reopened.tick(1100).status.mode, Mode::Hold);
    reopened.close(1100).unwrap();
    set_state(&path, Mode::Normal, "operator", "checked", 1200).unwrap();
    let mut clean = Enforcer::open(policy(), cfg(), 1200).unwrap();
    assert_eq!(clean.tick(1200).status.mode, Mode::Normal);
    clean.handle(fault("estop", Mode::EStop, 1210), 1210);
    clean.commit(1210).unwrap();
    drop(clean);
    let mut after = Enforcer::open(policy(), cfg(), 1300).unwrap();
    assert_eq!(after.tick(1300).status.mode, Mode::EStop);
}
