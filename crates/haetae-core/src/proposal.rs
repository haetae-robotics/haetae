use std::fmt;

use serde::{Deserialize, Serialize};

use crate::geom::Point2;

/// Where a proposal came from. Every source is untrusted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Vla,
    Planner,
    Teleop,
    Peer,
}

/// What the untrusted side wants the robot to do.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ActionKind {
    MoveTo { goal: Point2, speed: f64 },
    Grasp { object: String, at: Point2 },
    Place { at: Point2 },
    Stop,
}

impl ActionKind {
    /// The segment the robot would sweep, from `pose` to the action's target.
    /// `None` for `Stop`. Grasp and place are treated as reaching (or driving)
    /// to their target, so zones along the way are checked too.
    pub(crate) fn path(&self, pose: Point2) -> Option<(Point2, Point2)> {
        match self {
            ActionKind::MoveTo { goal: target, .. }
            | ActionKind::Grasp { at: target, .. }
            | ActionKind::Place { at: target } => Some((pose, *target)),
            ActionKind::Stop => None,
        }
    }

    pub(crate) fn speed(&self) -> Option<f64> {
        match self {
            ActionKind::MoveTo { speed, .. } => Some(*speed),
            _ => None,
        }
    }

    pub(crate) fn with_speed(&self, v: f64) -> ActionKind {
        match self {
            ActionKind::MoveTo { goal, .. } => ActionKind::MoveTo {
                goal: *goal,
                speed: v,
            },
            other => other.clone(),
        }
    }

    pub(crate) fn is_finite(&self) -> bool {
        match self {
            ActionKind::MoveTo { goal, speed } => {
                goal.is_finite() && speed.is_finite() && *speed >= 0.0
            }
            ActionKind::Grasp { at, .. } | ActionKind::Place { at } => at.is_finite(),
            ActionKind::Stop => true,
        }
    }
}

impl fmt::Display for ActionKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ActionKind::MoveTo { goal, speed } => {
                write!(f, "move_to({}, {}) @ {speed} m/s", goal.x, goal.y)
            }
            ActionKind::Grasp { object, at } => write!(f, "grasp({object} at {}, {})", at.x, at.y),
            ActionKind::Place { at } => write!(f, "place({}, {})", at.x, at.y),
            ActionKind::Stop => write!(f, "stop"),
        }
    }
}

/// An action proposed by an untrusted component.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ActionProposal {
    pub id: u64,
    pub source: Source,
    pub timestamp_ms: u64,
    pub action: ActionKind,
}
