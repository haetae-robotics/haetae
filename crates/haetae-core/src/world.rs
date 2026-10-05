use serde::{Deserialize, Serialize};

use crate::geom::Point2;
use crate::household::SemanticSnapshot;

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
    /// Trusted safety-observer facts. Never populated from a proposal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub semantic: Option<SemanticSnapshot>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RobotState {
    pub pose: Point2,
    /// Heading in the world frame, required for velocity commands.
    #[serde(default)]
    pub yaw: Option<f64>,
    /// Measured body-frame twist for compensating a delayed world snapshot.
    #[serde(default)]
    pub twist: Option<Twist2>,
    #[serde(default)]
    pub joints: Option<Vec<JointSample>>,
    /// Object currently in the gripper, if any.
    #[serde(default)]
    pub holding: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JointSample {
    pub name: String,
    pub position: f64,
    pub velocity: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Twist2 {
    pub linear: f64,
    pub angular: f64,
}

impl Twist2 {
    pub fn is_finite(self) -> bool {
        self.linear.is_finite() && self.angular.is_finite()
    }
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
            && self.robot.yaw.is_none_or(f64::is_finite)
            && self.robot.twist.is_none_or(Twist2::is_finite)
            && self.robot.joints.as_ref().is_none_or(|joints| {
                joints
                    .iter()
                    .all(|j| j.position.is_finite() && j.velocity.is_finite())
            })
            && (0.0..=1.0).contains(&self.confidence)
            && self
                .semantic
                .as_ref()
                .is_none_or(SemanticSnapshot::is_valid)
            && self.humans.iter().all(|h| h.pos.is_finite())
    }
}
