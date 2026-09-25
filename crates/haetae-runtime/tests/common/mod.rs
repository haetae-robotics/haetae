//! Shared fixtures for the runtime test suite.
// Not every test file uses every helper.
#![allow(dead_code)]

use haetae_core::{
    ActionKind, ActionProposal, Decision, Point2, Policy, RobotState, Source, WorldSnapshot,
};
use haetae_runtime::{Inbound, Outcome};
use serde_json::json;

/// Trusted receive time for the whole suite; worlds and proposals are
/// stamped at it so the freshness checks pass.
pub const NOW: u64 = 10_000;

/// Minimal policy: 10x10 m workspace, 1 m/s cap, vla/planner/teleop vetted.
pub const POLICY: &str = r#"{
  "envelope": { "max_speed": 1.0, "workspace": { "min": {"x":0,"y":0}, "max": {"x":10,"y":10} } },
  "allowed_sources": ["vla", "planner", "teleop"]
}"#;

pub fn policy() -> Policy {
    Policy::from_json(POLICY).expect("policy parses")
}

/// Robot at (1, 1), holding nothing, nobody around, confident perception.
pub fn world() -> WorldSnapshot {
    WorldSnapshot {
        stamp_ms: NOW,
        robot: RobotState {
            pose: Point2::new(1.0, 1.0),
            holding: None,
        },
        humans: Vec::new(),
        confidence: 0.95,
    }
}

/// A `move_to` proposal to (x, y) at `speed`, claimed at `NOW`.
pub fn proposal(id: u64, source: Source, x: f64, y: f64, speed: f64) -> Inbound {
    Inbound::Proposal(ActionProposal {
        id,
        source,
        timestamp_ms: NOW,
        action: ActionKind::MoveTo {
            goal: Point2::new(x, y),
            speed,
        },
    })
}

/// The same proposal as raw transport JSON, for `handle_bytes` tests.
pub fn proposal_bytes(id: u64, source: &str, x: f64, y: f64, speed: f64) -> Vec<u8> {
    json!({
        "id": id,
        "source": source,
        "timestamp_ms": NOW,
        "action": {"type": "move_to", "goal": {"x": x, "y": y}, "speed": speed},
    })
    .to_string()
    .into_bytes()
}

pub fn stop(id: u64, source: Source) -> Inbound {
    Inbound::Proposal(ActionProposal {
        id,
        source,
        timestamp_ms: NOW,
        action: ActionKind::Stop,
    })
}

/// Extract the [`Decision`] or fail loudly.
pub fn decide(outcome: Outcome) -> Decision {
    match outcome {
        Outcome::Decision(d) => d,
        other => panic!("expected a decision, got {other:?}"),
    }
}
