//! Gate behaviour on a small kitchen scene.
//!
//! Map (metres): workspace 0..10 × 0..10, a speed-limited hall at x 4..6,
//! and a no-entry child room at 7..10 × 7..10.

use haetae_core::*;

const POLICY: &str = r#"{
  "envelope": { "max_speed": 1.0, "workspace": { "min": {"x":0,"y":0}, "max": {"x":10,"y":10} } },
  "allowed_sources": ["vla", "planner", "teleop"],
  "zones": [
    { "id": "hall", "area": { "min": {"x":4,"y":0}, "max": {"x":6,"y":10} }, "speed_limit": 0.3 },
    { "id": "child-room", "area": { "min": {"x":7,"y":7}, "max": {"x":10,"y":10} }, "no_entry": true }
  ],
  "rules": [
    { "id": "knife-near-child",
      "when": { "object_any": ["knife"], "human_within": { "class": "child", "distance": 1.5 } },
      "then": "bul" },
    { "id": "slow-near-human",
      "when": { "human_within": { "distance": 1.0 } },
      "then": { "jeol": { "max_speed": 0.2 } } },
    { "id": "low-confidence",
      "when": { "confidence_below": 0.6 },
      "then": { "jeol": { "max_speed": 0.2 } } }
  ]
}"#;

fn gate() -> Gate {
    Gate::new(Policy::from_json(POLICY).expect("policy parses")).expect("policy is valid")
}

fn world() -> WorldSnapshot {
    WorldSnapshot {
        robot: RobotState {
            pose: Point2::new(1.0, 1.0),
            holding: None,
        },
        humans: Vec::new(),
        confidence: 0.95,
    }
}

fn child_at(x: f64, y: f64) -> Human {
    Human {
        id: "kid".into(),
        class: HumanClass::Child,
        pos: Point2::new(x, y),
    }
}

fn move_to(x: f64, y: f64, speed: f64) -> ActionProposal {
    propose(
        Source::Vla,
        ActionKind::MoveTo {
            goal: Point2::new(x, y),
            speed,
        },
    )
}

fn propose(source: Source, action: ActionKind) -> ActionProposal {
    ActionProposal {
        id: 1,
        source,
        timestamp_ms: 0,
        action,
    }
}

fn speed_of(d: &Decision) -> f64 {
    match d.action {
        Some(ActionKind::MoveTo { speed, .. }) => speed,
        ref other => panic!("expected move_to, got {other:?}"),
    }
}

#[test]
fn plain_move_is_yun() {
    let d = gate().judge(&move_to(3.0, 1.0, 0.5), &world());
    assert_eq!(d.verdict, Verdict::Yun);
    assert!(d.fired.is_empty());
}

#[test]
fn overspeed_is_clamped_to_envelope() {
    let d = gate().judge(&move_to(3.0, 1.0, 5.0), &world());
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(speed_of(&d), 1.0);
    assert_eq!(d.fired, ["envelope:max_speed"]);
}

#[test]
fn knife_near_child_is_bul() {
    let mut w = world();
    w.robot.holding = Some("knife".into());
    w.humans.push(child_at(2.0, 1.5));
    let d = gate().judge(&move_to(3.0, 1.0, 0.1), &w);
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.action, None);
    assert!(d.fired.contains(&"knife-near-child".to_string()));
}

#[test]
fn grasping_knife_counts_as_involving_it() {
    let mut w = world();
    w.humans.push(child_at(1.5, 1.0));
    let d = gate().judge(
        &propose(
            Source::Vla,
            ActionKind::Grasp {
                object: "knife".into(),
                at: Point2::new(1.2, 1.0),
            },
        ),
        &w,
    );
    assert_eq!(d.verdict, Verdict::Bul);
}

#[test]
fn knife_with_child_far_away_is_allowed() {
    let mut w = world();
    w.robot.holding = Some("knife".into());
    w.humans.push(child_at(3.0, 9.0));
    let d = gate().judge(&move_to(3.0, 1.0, 0.5), &w);
    assert_eq!(d.verdict, Verdict::Yun);
}

#[test]
fn entering_no_entry_zone_is_bul() {
    let d = gate().judge(&move_to(8.0, 8.0, 0.2), &world());
    assert_eq!(d.verdict, Verdict::Bul);
    assert!(d.fired.contains(&"zone:child-room".to_string()));
}

#[test]
fn crossing_hall_is_clamped() {
    let d = gate().judge(&move_to(8.0, 1.0, 0.8), &world());
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(speed_of(&d), 0.3);
    assert_eq!(d.fired, ["zone:hall"]);
}

#[test]
fn strictest_cap_wins() {
    let mut w = world();
    w.humans.push(Human {
        id: "mom".into(),
        class: HumanClass::Adult,
        pos: Point2::new(5.0, 1.5),
    });
    let d = gate().judge(&move_to(8.0, 1.0, 0.8), &w);
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(speed_of(&d), 0.2);
    assert_eq!(d.fired, ["zone:hall", "slow-near-human"]);
}

#[test]
fn disallowed_source_is_bul() {
    let d = gate().judge(
        &propose(
            Source::Peer,
            ActionKind::Place {
                at: Point2::new(2.0, 2.0),
            },
        ),
        &world(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["source:not-allowed"]);
}

#[test]
fn stop_is_always_allowed() {
    let mut g = gate();
    g.raise_mode(Mode::EStop);
    let d = g.judge(&propose(Source::Peer, ActionKind::Stop), &world());
    assert_eq!(d.verdict, Verdict::Yun);
    assert_eq!(d.fired, ["stop:unvetted-source"]);
}

#[test]
fn low_confidence_is_clamped() {
    let mut w = world();
    w.confidence = 0.4;
    let d = gate().judge(&move_to(3.0, 1.0, 0.5), &w);
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(speed_of(&d), 0.2);
}

#[test]
fn goal_outside_workspace_is_bul() {
    let d = gate().judge(&move_to(12.0, 1.0, 0.5), &world());
    assert_eq!(d.verdict, Verdict::Bul);
    assert!(d.fired.contains(&"envelope:workspace".to_string()));
}

#[test]
fn non_finite_input_is_bul() {
    let d = gate().judge(&move_to(3.0, 1.0, f64::NAN), &world());
    assert_eq!(d.fired, ["invalid:proposal"]);

    let mut w = world();
    w.confidence = f64::NAN;
    let d = gate().judge(&move_to(3.0, 1.0, 0.5), &w);
    assert_eq!(d.fired, ["invalid:world"]);
}

#[test]
fn mode_ladder_only_goes_up() {
    let mut g = gate();
    assert!(g.raise_mode(Mode::Hold));
    assert!(!g.raise_mode(Mode::Caution));
    assert_eq!(g.mode(), Mode::Hold);

    let d = g.judge(&move_to(3.0, 1.0, 0.5), &world());
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["mode:hold"]);

    g.reset_mode();
    assert_eq!(g.mode(), Mode::Normal);
}

#[test]
fn caution_halves_the_envelope() {
    let mut g = gate();
    g.raise_mode(Mode::Caution);
    let d = g.judge(&move_to(3.0, 1.0, 0.9), &world());
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(speed_of(&d), 0.5);
    assert_eq!(d.mode, Mode::Caution);
}

// --- Regressions from Devin's W1 review ------------------------------------

/// H1: grasp and place sweep from the robot to their target, so a no-entry
/// zone on the way cannot be skipped by using a non-move action.
#[test]
fn place_through_no_entry_zone_is_bul() {
    let mut w = world();
    w.robot.pose = Point2::new(6.5, 6.5);
    let place = propose(
        Source::Vla,
        ActionKind::Place {
            at: Point2::new(9.5, 9.5),
        },
    );
    let d = gate().judge(&place, &w);
    assert_eq!(d.verdict, Verdict::Bul);
    assert!(d.fired.contains(&"zone:child-room".to_string()));

    let grasp = propose(
        Source::Vla,
        ActionKind::Grasp {
            object: "toy".into(),
            at: Point2::new(9.5, 9.5),
        },
    );
    assert_eq!(gate().judge(&grasp, &w).verdict, Verdict::Bul);
}

/// H2: a speed cap on an action without a speed field must not vanish.
#[test]
fn cap_on_speedless_action_is_jeol_with_speed_cap() {
    let mut w = world();
    w.humans.push(Human {
        id: "mom".into(),
        class: HumanClass::Adult,
        pos: Point2::new(1.5, 1.0),
    });
    let grasp = propose(
        Source::Vla,
        ActionKind::Grasp {
            object: "spatula".into(),
            at: Point2::new(1.3, 1.0),
        },
    );
    let d = gate().judge(&grasp, &w);
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(d.speed_cap, Some(0.2));
    assert_eq!(d.action, Some(grasp.action.clone()));

    let mut g = gate();
    g.raise_mode(Mode::Caution);
    let d = g.judge(
        &propose(
            Source::Vla,
            ActionKind::Place {
                at: Point2::new(2.0, 1.0),
            },
        ),
        &world(),
    );
    assert_eq!(d.verdict, Verdict::Jeol);
    assert_eq!(d.speed_cap, Some(0.5));
}

#[test]
fn uncapped_decisions_carry_no_speed_cap() {
    let d = gate().judge(&move_to(3.0, 1.0, 0.5), &world());
    assert_eq!(d.speed_cap, None);
}

/// M2: a robot already outside the workspace is not allowed to drive on.
#[test]
fn pose_outside_workspace_is_bul() {
    let mut w = world();
    w.robot.pose = Point2::new(-5.0, 5.0);
    let d = gate().judge(&move_to(3.0, 1.0, 0.5), &w);
    assert_eq!(d.verdict, Verdict::Bul);
    assert!(d.fired.contains(&"envelope:pose".to_string()));
}
