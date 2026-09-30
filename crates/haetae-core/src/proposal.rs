use std::fmt;

use serde::{Deserialize, Serialize};

use crate::geom::Point2;
use crate::world::Twist2;

/// Where a proposal came from. Every source is untrusted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
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
    MoveTo {
        goal: Point2,
        speed: f64,
    },
    Grasp {
        object: String,
        at: Point2,
    },
    Place {
        at: Point2,
    },
    Stop,
    /// Body-frame command for a differential drive base.
    Velocity {
        linear: f64,
        angular: f64,
        ttl_ms: u64,
    },
    /// Ordered joint waypoints. Each segment is checked against the trusted
    /// measured joint state; execution still requires a monitored adapter.
    JointTrajectory {
        points: Vec<JointWaypoint>,
        ttl_ms: u64,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JointWaypoint {
    pub time_from_start_ms: u64,
    pub positions: Vec<f64>,
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
            ActionKind::Stop | ActionKind::Velocity { .. } | ActionKind::JointTrajectory { .. } => {
                None
            }
        }
    }

    pub(crate) fn speed(&self) -> Option<f64> {
        match self {
            ActionKind::MoveTo { speed, .. } => Some(*speed),
            ActionKind::Velocity { linear, .. } => Some(linear.abs()),
            _ => None,
        }
    }

    pub(crate) fn with_speed(&self, v: f64) -> ActionKind {
        match self {
            ActionKind::MoveTo { goal, .. } => ActionKind::MoveTo {
                goal: *goal,
                speed: v,
            },
            ActionKind::Velocity {
                linear,
                angular,
                ttl_ms,
            } => {
                let factor = if *linear == 0.0 {
                    1.0
                } else {
                    v / linear.abs()
                };
                ActionKind::Velocity {
                    linear: linear * factor,
                    angular: angular * factor,
                    ttl_ms: *ttl_ms,
                }
            }
            other => other.clone(),
        }
    }

    pub(crate) fn is_finite(&self) -> bool {
        match self {
            ActionKind::MoveTo { goal, speed } => {
                goal.is_finite() && speed.is_finite() && *speed >= 0.0
            }
            ActionKind::Grasp { at, .. } | ActionKind::Place { at } => at.is_finite(),
            ActionKind::Velocity {
                linear, angular, ..
            } => Twist2 {
                linear: *linear,
                angular: *angular,
            }
            .is_finite(),
            ActionKind::JointTrajectory { points, .. } => points
                .iter()
                .all(|p| p.positions.iter().all(|x| x.is_finite())),
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
            ActionKind::Velocity {
                linear,
                angular,
                ttl_ms,
            } => write!(f, "velocity({linear} m/s, {angular} rad/s, {ttl_ms} ms)"),
            ActionKind::JointTrajectory { points, ttl_ms } => {
                write!(f, "joint_trajectory({} points, {ttl_ms} ms)", points.len())
            }
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
