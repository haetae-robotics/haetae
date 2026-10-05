//! Mandatory household checks for the pinned, stationary four-joint lab scope.
//!
//! Facts are accepted only through the trusted world channel. Proposals contain
//! references, never material/device facts or an asserted Cartesian path. The
//! root-loaded policy owns FK and the conservative end-effector/item envelope;
//! this is not whole-arm collision coverage or real grasp/effect verification.
use serde::{Deserialize, Serialize};

use crate::hazard::{self, HazardRequest, ItemKind, Point3, Region, RegionKind, State};
use crate::{
    ActionKind, ActionProposal, JointWaypoint, Point2, Policy, PolicyError, WorldSnapshot,
};

const MAX_ID: usize = 64;
const MAX_CHAIN: usize = 32;
const MAX_PATH: usize = 4096;
const MAX_AGE_MS: u64 = 200;
const SAMPLE_STEP_RAD: f64 = 0.002;
const SWEPT_RADIUS_M: f64 = 0.15;
const BASE_DRIFT: f64 = 0.002;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum JointAxis {
    Y,
    Z,
}

/// URDF origin transform, followed by an optional local joint rotation.
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ChainTransform {
    pub xyz: [f64; 3],
    pub rpy: [f64; 3],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub joint_index: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub axis: Option<JointAxis>,
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HouseholdPolicy {
    pub schema_version: u8,
    pub robot_id: String,
    pub model_sha256: String,
    pub tool_id: String,
    pub chain: Vec<ChainTransform>,
    pub sample_step_rad: f64,
    pub swept_radius_m: f64,
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticSnapshot {
    pub schema_version: u8,
    pub revision: u64,
    pub task_revision: u64,
    pub task_id: String,
    pub step_id: String,
    pub robot_id: String,
    pub model_sha256: String,
    pub tool_id: String,
    pub item_id: String,
    pub observed_ms: u64,
    pub confidence: f64,
    pub coverage_known: bool,
    pub item: ItemKind,
    pub regions: Vec<Region>,
    /// Stationary-base anchor for this semantic scene, not proposal metadata.
    pub base_pose: Point2,
    pub base_yaw: f64,
}

/// Untrusted identity/version references into the trusted semantic snapshot.
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticBinding {
    pub schema_version: u8,
    pub world_revision: u64,
    pub task_revision: u64,
    pub task_id: String,
    pub step_id: String,
    pub robot_id: String,
    pub model_sha256: String,
    pub tool_id: String,
    pub item_id: String,
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= MAX_ID
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
}

fn valid_sha(sha: &str) -> bool {
    sha.len() == 64
        && sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl HouseholdPolicy {
    pub(crate) fn validate(&self, policy: &Policy) -> Result<(), PolicyError> {
        let invalid =
            || PolicyError::Invalid("household configuration exceeds pinned lab bounds".into());
        let arm = policy.arm.as_ref().ok_or_else(invalid)?;
        if self.schema_version != 1
            || !valid_id(&self.robot_id)
            || !valid_sha(&self.model_sha256)
            || !valid_id(&self.tool_id)
            || self.sample_step_rad != SAMPLE_STEP_RAD
            || self.swept_radius_m != SWEPT_RADIUS_M
            || self.chain.is_empty()
            || self.chain.len() > MAX_CHAIN
            || policy.freshness.world_max_age_ms > MAX_AGE_MS
            || arm.joints.len() != 4
            || arm.max_duration_ms > 1000
            || arm.max_start_error > 0.01
            || arm.max_tracking_error > 0.05
            || arm.min_confidence < 0.9
        {
            return Err(invalid());
        }
        let bounds = [
            (-0.8 * std::f64::consts::PI, std::f64::consts::PI),
            (-std::f64::consts::FRAC_PI_2, std::f64::consts::FRAC_PI_2),
            (-1.5, 1.4),
            (-1.7, 1.97),
        ];
        for (i, joint) in arm.joints.iter().enumerate() {
            if joint.name != format!("joint{}", i + 1)
                || joint.min_position < bounds[i].0
                || joint.max_position > bounds[i].1
                || joint.max_velocity > 1.0
                || joint.max_acceleration > 40.0
            {
                return Err(invalid());
            }
        }
        let mut indexed = [false; 4];
        let mut next_joint = 0;
        for transform in &self.chain {
            if transform
                .xyz
                .iter()
                .any(|v| !v.is_finite() || v.abs() > 1.0)
                || transform
                    .rpy
                    .iter()
                    .any(|v| !v.is_finite() || v.abs() > std::f64::consts::TAU)
            {
                return Err(invalid());
            }
            match (transform.joint_index, transform.axis) {
                (None, None) => (),
                (Some(i), Some(_)) if i < 4 && i == next_joint => {
                    indexed[i] = true;
                    next_joint += 1;
                }
                _ => return Err(invalid()),
            }
        }
        if indexed.contains(&false) {
            return Err(invalid());
        }
        // Sum of downstream link lengths bounds the displacement caused by
        // four <=0.05-rad tracking errors. Preserve the lab's <=0.07-m budget.
        let mut downstream = 0.0;
        let mut lever_sum = 0.0;
        for transform in self.chain.iter().rev() {
            if transform.joint_index.is_some() {
                lever_sum += downstream;
            }
            downstream += transform.xyz.iter().map(|x| x * x).sum::<f64>().sqrt();
        }
        if lever_sum > 1.4 || downstream > 1.0 {
            return Err(invalid());
        }
        Ok(())
    }
}

impl SemanticSnapshot {
    /// Bounded structural validation; freshness and matching are gate checks.
    pub fn is_valid(&self) -> bool {
        self.schema_version == 1
            && self.revision > 0
            && self.task_revision > 0
            && [
                &self.task_id,
                &self.step_id,
                &self.robot_id,
                &self.tool_id,
                &self.item_id,
            ]
            .iter()
            .all(|id| valid_id(id))
            && valid_sha(&self.model_sha256)
            && self.confidence.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.base_pose.is_finite()
            && self.base_pose.x.abs() <= 100.0
            && self.base_pose.y.abs() <= 100.0
            && self.base_yaw.is_finite()
            && self.base_yaw.abs() <= std::f64::consts::TAU
            && self.regions.len() <= 128
            && {
                let mut ids = std::collections::HashSet::new();
                self.regions.iter().all(|region| {
                    valid_id(&region.id)
                        && ids.insert(&region.id)
                        && region.contents.len() <= 32
                        && [region.bounds.min, region.bounds.max].iter().all(|p| {
                            [p.x, p.y, p.z]
                                .iter()
                                .all(|v| v.is_finite() && v.abs() <= 100.0)
                        })
                        && region.bounds.min.x <= region.bounds.max.x
                        && region.bounds.min.y <= region.bounds.max.y
                        && region.bounds.min.z <= region.bounds.max.z
                })
            }
    }

    /// Observation refresh may change time/confidence, never scene identity or
    /// safety facts without advancing the revision.
    pub fn same_revision_facts(&self, other: &Self) -> bool {
        self.schema_version == other.schema_version
            && self.revision == other.revision
            && self.task_revision == other.task_revision
            && self.task_id == other.task_id
            && self.step_id == other.step_id
            && self.robot_id == other.robot_id
            && self.model_sha256 == other.model_sha256
            && self.tool_id == other.tool_id
            && self.item_id == other.item_id
            && self.coverage_known == other.coverage_known
            && self.item == other.item
            && self.regions == other.regions
            && self.base_pose == other.base_pose
            && self.base_yaw == other.base_yaw
    }
}

fn matching_identity(binding: &SemanticBinding, scene: &SemanticSnapshot) -> bool {
    binding.schema_version == 1
        && binding.task_revision == scene.task_revision
        && binding.task_id == scene.task_id
        && binding.step_id == scene.step_id
        && binding.robot_id == scene.robot_id
        && binding.model_sha256 == scene.model_sha256
        && binding.tool_id == scene.tool_id
        && binding.item_id == scene.item_id
}

fn checked_scene<'a>(
    policy: &Policy,
    proposal: &ActionProposal,
    world: &'a WorldSnapshot,
    now_ms: u64,
    original: Option<&SemanticSnapshot>,
) -> Result<(&'a SemanticSnapshot, &'a [crate::JointSample]), String> {
    let h = policy.household.as_ref().ok_or("household:no-policy")?;
    h.validate(policy).map_err(|_| "household:invalid-policy")?;
    if !matches!(proposal.action, ActionKind::JointTrajectory { .. }) {
        return Err("household:unsupported-action".into());
    }
    let binding = proposal
        .semantic
        .as_ref()
        .ok_or("household:missing-binding")?;
    let scene = world.semantic.as_ref().ok_or("household:missing-world")?;
    if !world.is_valid() || !scene.is_valid() {
        return Err("household:invalid-world".into());
    }
    if world.stamp_ms > now_ms
        || now_ms - world.stamp_ms > MAX_AGE_MS
        || scene.observed_ms > now_ms
        || scene.observed_ms > world.stamp_ms
        || now_ms - scene.observed_ms > MAX_AGE_MS
    {
        return Err("household:stale-world".into());
    }
    if !matching_identity(binding, scene)
        || scene.robot_id != h.robot_id
        || scene.model_sha256 != h.model_sha256
        || scene.tool_id != h.tool_id
        || binding.world_revision == 0
        || match original {
            None => binding.world_revision != scene.revision,
            Some(initial) => {
                !initial.is_valid()
                    || !matching_identity(binding, initial)
                    || binding.world_revision != initial.revision
                    || scene.revision < initial.revision
                    || (scene.revision == initial.revision && !scene.same_revision_facts(initial))
            }
        }
    {
        return Err("household:binding-mismatch".into());
    }
    if scene.item == ItemKind::Unknown
        || scene.regions.iter().any(|r| {
            r.kind == RegionKind::Unknown
                || r.state == State::Unknown
                || r.contents.contains(&ItemKind::Unknown)
        })
    {
        return Err("household:unknown-facts".into());
    }
    let anchor = original.unwrap_or(scene);
    let yaw = world.robot.yaw.ok_or("household:missing-base")?;
    let twist = world.robot.twist.ok_or("household:missing-base")?;
    let yaw_delta = |a: f64, b: f64| (a - b).sin().atan2((a - b).cos()).abs();
    let pose_delta = |a: Point2, b: Point2| ((a.x - b.x).powi(2) + (a.y - b.y).powi(2)).sqrt();
    if twist.linear.abs() >= 0.01
        || twist.angular.abs() >= 0.01
        || pose_delta(world.robot.pose, anchor.base_pose) > BASE_DRIFT
        || yaw_delta(yaw, anchor.base_yaw) > BASE_DRIFT
        || pose_delta(scene.base_pose, anchor.base_pose) > BASE_DRIFT
        || yaw_delta(scene.base_yaw, anchor.base_yaw) > BASE_DRIFT
    {
        return Err("household:base-moving".into());
    }
    let measured = world
        .robot
        .joints
        .as_ref()
        .ok_or("household:missing-joints")?;
    let arm = policy.arm.as_ref().ok_or("household:no-arm")?;
    if measured.len() != 4
        || measured.iter().zip(&arm.joints).any(|(sample, limit)| {
            sample.name != limit.name
                || !sample.position.is_finite()
                || sample.position < limit.min_position
                || sample.position > limit.max_position
        })
    {
        return Err("household:invalid-joints".into());
    }
    Ok((scene, measured))
}

/// Admission check. FK is recomputed from the exact joint waypoints in the
/// candidate and trusted measured base/joints; submitted paths are impossible.
pub fn check_proposal(
    policy: &Policy,
    proposal: &ActionProposal,
    world: &WorldSnapshot,
    now_ms: u64,
) -> Result<(), String> {
    if policy.household.is_none() {
        return Ok(());
    }
    let (scene, measured) = checked_scene(policy, proposal, world, now_ms, None)?;
    let ActionKind::JointTrajectory { points, .. } = &proposal.action else {
        return Err("household:unsupported-action".into());
    };
    let measured: Vec<f64> = measured.iter().map(|j| j.position).collect();
    evaluate_path(policy, world, scene, &measured, points, now_ms)
}

/// Recheck the remaining admitted path. The initial scene anchors identity and
/// base position; a newer scene revision is allowed only after fresh hazard
/// evaluation. Already consumed waypoints are excluded. Tracking and ordinary
/// arm limits remain enforced by the active command monitor.
pub fn recheck_active(
    policy: &Policy,
    proposal: &ActionProposal,
    original: &SemanticSnapshot,
    world: &WorldSnapshot,
    started_ms: u64,
    now_ms: u64,
) -> Result<(), String> {
    if policy.household.is_none() {
        return Ok(());
    }
    let (scene, measured) = checked_scene(policy, proposal, world, now_ms, Some(original))?;
    let ActionKind::JointTrajectory { points, ttl_ms } = &proposal.action else {
        return Err("household:unsupported-action".into());
    };
    if now_ms < started_ms || now_ms - started_ms >= *ttl_ms {
        return Err("household:expired".into());
    }
    validate_points(policy, points)?;
    // Observations can trail the runtime clock. Include the path remaining at
    // observation time, which conservatively includes that delay interval.
    let elapsed = world.stamp_ms.saturating_sub(started_ms);
    let expected = positions_at(points, elapsed);
    let mut remaining = vec![JointWaypoint {
        time_from_start_ms: elapsed,
        positions: expected,
    }];
    remaining.extend(
        points
            .iter()
            .filter(|p| p.time_from_start_ms > elapsed)
            .cloned(),
    );
    if remaining.len() == 1 {
        remaining.push(remaining[0].clone());
    }
    let measured: Vec<f64> = measured.iter().map(|j| j.position).collect();
    evaluate_path(policy, world, scene, &measured, &remaining, now_ms)
}

fn validate_points(policy: &Policy, points: &[JointWaypoint]) -> Result<(), String> {
    let arm = policy.arm.as_ref().ok_or("household:no-arm")?;
    if points.is_empty()
        || points.len() > arm.max_points
        || points
            .iter()
            .any(|p| p.positions.len() != 4 || p.positions.iter().any(|q| !q.is_finite()))
        || points
            .windows(2)
            .any(|p| p[0].time_from_start_ms > p[1].time_from_start_ms)
        || points.iter().any(|p| {
            p.positions
                .iter()
                .zip(&arm.joints)
                .any(|(q, j)| *q < j.min_position || *q > j.max_position)
        })
    {
        return Err("household:invalid-points".into());
    }
    Ok(())
}

fn positions_at(points: &[JointWaypoint], elapsed: u64) -> Vec<f64> {
    for pair in points.windows(2) {
        if elapsed <= pair[1].time_from_start_ms {
            let span = pair[1].time_from_start_ms - pair[0].time_from_start_ms;
            let fraction = if span == 0 {
                0.0
            } else {
                elapsed.saturating_sub(pair[0].time_from_start_ms) as f64 / span as f64
            };
            return pair[0]
                .positions
                .iter()
                .zip(&pair[1].positions)
                .map(|(a, b)| a + (b - a) * fraction)
                .collect();
        }
    }
    points
        .last()
        .expect("validated nonempty points")
        .positions
        .clone()
}

fn evaluate_path(
    policy: &Policy,
    world: &WorldSnapshot,
    scene: &SemanticSnapshot,
    measured: &[f64],
    points: &[JointWaypoint],
    now_ms: u64,
) -> Result<(), String> {
    validate_points(policy, points)?;
    let h = policy.household.as_ref().ok_or("household:no-policy")?;
    let yaw = world.robot.yaw.ok_or("household:missing-base")?;
    let mut path = vec![fk(h, measured, world.robot.pose, yaw)];
    // The measured-to-expected connector accounts for tracking/start offset.
    let mut previous = measured;
    for point in points {
        let steps = previous
            .iter()
            .zip(&point.positions)
            .map(|(a, b)| (a - b).abs())
            .fold(0.0, f64::max);
        let count = (steps / h.sample_step_rad).ceil().max(1.0) as usize;
        if count > MAX_PATH || path.len().saturating_add(count) > MAX_PATH {
            return Err("household:path-budget".into());
        }
        for step in 1..=count {
            let t = step as f64 / count as f64;
            let joints: Vec<f64> = previous
                .iter()
                .zip(&point.positions)
                .map(|(a, b)| a + (b - a) * t)
                .collect();
            path.push(fk(h, &joints, world.robot.pose, yaw));
        }
        previous = &point.positions;
    }
    let decision = hazard::judge(&HazardRequest {
        now_ms,
        observed_ms: scene.observed_ms,
        confidence: scene.confidence,
        coverage_known: scene.coverage_known,
        item: scene.item,
        observed_start: path[0],
        swept_radius_m: h.swept_radius_m,
        path,
        regions: scene.regions.clone(),
    });
    if decision.allowed {
        Ok(())
    } else {
        Err(format!("household:{}", decision.reason))
    }
}

type Matrix = [[f64; 4]; 4];
fn transform(xyz: [f64; 3], rpy: [f64; 3]) -> Matrix {
    let (sr, cr) = rpy[0].sin_cos();
    let (sp, cp) = rpy[1].sin_cos();
    let (sy, cy) = rpy[2].sin_cos();
    [
        [
            cy * cp,
            cy * sp * sr - sy * cr,
            cy * sp * cr + sy * sr,
            xyz[0],
        ],
        [
            sy * cp,
            sy * sp * sr + cy * cr,
            sy * sp * cr - cy * sr,
            xyz[1],
        ],
        [-sp, cp * sr, cp * cr, xyz[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]
}
fn multiply(a: Matrix, b: Matrix) -> Matrix {
    let mut result = [[0.0; 4]; 4];
    for (i, row) in result.iter_mut().enumerate() {
        for (j, value) in row.iter_mut().enumerate() {
            *value = (0..4).map(|k| a[i][k] * b[k][j]).sum();
        }
    }
    result
}
fn fk(policy: &HouseholdPolicy, joints: &[f64], pose: Point2, yaw: f64) -> Point3 {
    let mut matrix = transform([pose.x, pose.y, 0.0], [0.0, 0.0, yaw]);
    for origin in &policy.chain {
        matrix = multiply(matrix, transform(origin.xyz, origin.rpy));
        if let (Some(index), Some(axis)) = (origin.joint_index, origin.axis) {
            let q = joints[index];
            let rotation = match axis {
                JointAxis::Y => [0.0, q, 0.0],
                JointAxis::Z => [0.0, 0.0, q],
            };
            matrix = multiply(matrix, transform([0.0; 3], rotation));
        }
    }
    Point3 {
        x: matrix[0][3],
        y: matrix[1][3],
        z: matrix[2][3],
    }
}
