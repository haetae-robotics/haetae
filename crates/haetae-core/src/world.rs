use serde::{Deserialize, Serialize};

use crate::geom::Point2;

/// Facts from the trusted safety-perception path. The gate never takes
/// world facts from a proposal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorldSnapshot {
    /// When the safety-perception path produced this snapshot (ms, runtime clock domain).
    pub stamp_ms: u64,
    pub robot: RobotState,
    #[serde(default)]
    pub humans: Vec<Human>,
    /// Overall confidence of the safety perception, 0.0–1.0.
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RobotState {
    pub pose: Point2,
    /// Object currently in the gripper, if any.
    #[serde(default)]
    pub holding: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Human {
    pub id: String,
    pub class: HumanClass,
    pub pos: Point2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HumanClass {
    Adult,
    Child,
}

impl WorldSnapshot {
    /// Finite pose and positions, confidence within 0..=1.
    pub fn is_valid(&self) -> bool {
        self.robot.pose.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.humans.iter().all(|h| h.pos.is_finite())
    }
}
