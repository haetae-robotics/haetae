//! Durable facts and pure-motion history for the fixed-gripper household scope.
//! This never infers material transfer, grasp success or task success from FK.
use std::collections::BTreeMap;

use haetae_core::hazard::{ItemKind, Region, RegionKind};
use haetae_core::household::SemanticSnapshot;
use haetae_core::{ActionKind, ActionProposal, Policy, Source, WorldSnapshot};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MAX_ITEMS: usize = 256;
const MAX_CONTAINERS: usize = 64;
const MAX_REGIONS: usize = 512;
const MAX_STEPS: usize = 64;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct History {
    v: u8,
    robot_id: String,
    model_sha256: String,
    tool_id: String,
    floor: Option<SemanticSnapshot>,
    items: BTreeMap<String, ItemKind>,
    containers: BTreeMap<String, Region>,
    region_kinds: BTreeMap<String, RegionKind>,
    step_epoch: u64,
    steps: BTreeMap<String, MotionRecord>,
    pending: Option<String>,
    blocked_epoch: u64,
    // Observation samples are not persisted authority; always reset on restart.
    #[serde(skip)]
    settle_stamp: u64,
    #[serde(skip)]
    settle_samples: u8,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MotionRecord {
    task_revision: u64,
    task_id: String,
    step_id: String,
    item_id: String,
    proposal_id: u64,
    source: Source,
    command_sha256: String,
    started_ms: u64,
    end_ms: u64,
    final_positions: Vec<f64>,
    outcome: MotionOutcome,
    stopped_ms: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum MotionOutcome {
    Reserved,
    AwaitingStop,
    MotionSettled,
    Interrupted,
}

#[derive(Debug, Clone, Serialize)]
pub struct HistoryStatus {
    pub scene_revision: Option<u64>,
    pub task_revision: Option<u64>,
    pub observed_items: usize,
    pub retained_containers: usize,
    pub observed_regions: usize,
    pub retained_contaminants: usize,
    pub settled_motions: usize,
    pub interrupted_motions: usize,
    pub consumed_steps: usize,
    pub motion_pending: bool,
    pub blocked_task_revision: u64,
    /// Kinematic evidence only. There is no material-effect commit API.
    pub material_effects_committed: u64,
}

fn id_valid(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
}

fn step_key(task: &str, step: &str) -> String {
    format!("{task}/{step}")
}

impl History {
    pub fn new(policy: &Policy) -> Option<Self> {
        let pins = policy.household.as_ref()?;
        Some(Self {
            v: 1,
            robot_id: pins.robot_id.clone(),
            model_sha256: pins.model_sha256.clone(),
            tool_id: pins.tool_id.clone(),
            floor: None,
            items: BTreeMap::new(),
            containers: BTreeMap::new(),
            region_kinds: BTreeMap::new(),
            step_epoch: 0,
            steps: BTreeMap::new(),
            pending: None,
            blocked_epoch: 0,
            settle_stamp: 0,
            settle_samples: 0,
        })
    }

    pub fn validate(&self, policy: &Policy) -> Result<(), String> {
        let pins = policy.household.as_ref().ok_or("history:missing-policy")?;
        if self.v != 1
            || self.robot_id != pins.robot_id
            || self.model_sha256 != pins.model_sha256
            || self.tool_id != pins.tool_id
            || self.items.len() > MAX_ITEMS
            || self.containers.len() > MAX_CONTAINERS
            || self.region_kinds.len() > MAX_REGIONS
            || self.region_kinds.keys().any(|id| !id_valid(id))
            || self.items.values().any(|kind| *kind == ItemKind::Unknown)
            || self.steps.len() > MAX_STEPS
            || self.items.keys().any(|id| !id_valid(id))
            || self.containers.iter().any(|(id, r)| {
                id != &r.id
                    || !id_valid(id)
                    || r.kind != RegionKind::Container
                    || r.contents.len() > 32
                    || r.contents.contains(&ItemKind::Unknown)
                    || self.region_kinds.get(id) != Some(&RegionKind::Container)
                    || ![r.bounds.min, r.bounds.max].iter().all(|p| {
                        [p.x, p.y, p.z]
                            .iter()
                            .all(|n| n.is_finite() && n.abs() <= 100.0)
                    })
                    || r.bounds.min.x > r.bounds.max.x
                    || r.bounds.min.y > r.bounds.max.y
                    || r.bounds.min.z > r.bounds.max.z
            })
            || self.floor.as_ref().is_some_and(|s| {
                !s.is_valid()
                    || s.robot_id != self.robot_id
                    || s.model_sha256 != self.model_sha256
                    || s.tool_id != self.tool_id
            })
            || self.steps.iter().any(|(key, r)| {
                key != &step_key(&r.task_id, &r.step_id)
                    || !id_valid(&r.task_id)
                    || !id_valid(&r.step_id)
                    || !id_valid(&r.item_id)
                    || r.task_revision != self.step_epoch
                    || r.final_positions.len() != 4
                    || r.final_positions.iter().any(|p| !p.is_finite())
                    || r.end_ms < r.started_ms
                    || r.command_sha256.len() != 64
                    || !r
                        .command_sha256
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
            || self
                .pending
                .as_ref()
                .is_some_and(|k| !self.steps.contains_key(k))
        {
            return Err("history:invalid-state-or-pins".into());
        }
        let floor_epoch = self.floor.as_ref().map_or(0, |s| s.task_revision);
        if self.step_epoch > floor_epoch
            || self.blocked_epoch > floor_epoch
            || (self.floor.is_none()
                && (!self.items.is_empty()
                    || !self.containers.is_empty()
                    || !self.region_kinds.is_empty()
                    || !self.steps.is_empty()))
            || self.steps.iter().any(|(key, r)| {
                ((r.outcome == MotionOutcome::Reserved
                    || r.outcome == MotionOutcome::AwaitingStop
                    || (r.outcome == MotionOutcome::Interrupted && r.stopped_ms.is_none()))
                    && self.pending.as_ref() != Some(key))
                    || (r.outcome == MotionOutcome::MotionSettled && r.stopped_ms.is_none())
                    || (self.pending.as_ref() == Some(key) && r.stopped_ms.is_some())
                    || r.end_ms.saturating_sub(r.started_ms) > 1000
            })
        {
            return Err("history:inconsistent-checkpoint".into());
        }
        Ok(())
    }

    pub fn restart(&mut self) {
        self.interrupt();
        self.settle_stamp = 0;
        self.settle_samples = 0;
    }

    pub fn status(&self) -> HistoryStatus {
        HistoryStatus {
            scene_revision: self.floor.as_ref().map(|s| s.revision),
            task_revision: self.floor.as_ref().map(|s| s.task_revision),
            observed_items: self.items.len(),
            retained_containers: self.containers.len(),
            observed_regions: self.region_kinds.len(),
            retained_contaminants: self.containers.values().map(|r| r.contents.len()).sum(),
            settled_motions: self
                .steps
                .values()
                .filter(|r| r.outcome == MotionOutcome::MotionSettled)
                .count(),
            interrupted_motions: self
                .steps
                .values()
                .filter(|r| r.outcome == MotionOutcome::Interrupted)
                .count(),
            consumed_steps: self.steps.len(),
            motion_pending: self.pending.is_some(),
            blocked_task_revision: self.blocked_epoch,
            material_effects_committed: 0,
        }
    }

    pub fn check_world(&self, world: &WorldSnapshot) -> Result<(), String> {
        let Some(next) = world.semantic.as_ref() else {
            return Ok(());
        };
        // Leave ordinary structural/freshness errors to the existing gate.
        if !next.is_valid() {
            return Ok(());
        }
        if next.robot_id != self.robot_id
            || next.model_sha256 != self.model_sha256
            || next.tool_id != self.tool_id
        {
            return Err("history:pin-mismatch".into());
        }
        if let Some(old) = &self.floor {
            if next.revision < old.revision
                || next.task_revision < old.task_revision
                || next.observed_ms < old.observed_ms
            {
                return Err("history:observation-rollback".into());
            }
            if next.revision == old.revision && !next.same_revision_facts(old) {
                return Err("history:same-revision-changed-facts".into());
            }
            if (next.task_id != old.task_id || next.step_id != old.step_id)
                && next.task_revision == old.task_revision
            {
                return Err("history:task-revision-required".into());
            }
        }
        if self
            .items
            .get(&next.item_id)
            .is_some_and(|kind| next.item != ItemKind::Unknown && kind != &next.item)
        {
            return Err("history:item-identity-changed".into());
        }
        if !self.items.contains_key(&next.item_id) && self.items.len() >= MAX_ITEMS {
            return Err("history:item-capacity".into());
        }
        let new_regions = next
            .regions
            .iter()
            .filter(|r| !self.region_kinds.contains_key(&r.id))
            .count();
        if self.region_kinds.len() + new_regions > MAX_REGIONS {
            return Err("history:region-capacity".into());
        }
        if next.regions.iter().any(|r| {
            self.region_kinds
                .get(&r.id)
                .is_some_and(|kind| kind != &r.kind)
                && r.kind != RegionKind::Unknown
        }) {
            return Err("history:region-identity-changed".into());
        }
        let new_containers = next
            .regions
            .iter()
            .filter(|r| r.kind == RegionKind::Container && !self.containers.contains_key(&r.id))
            .count();
        if self.containers.len() + new_containers > MAX_CONTAINERS {
            return Err("history:container-capacity".into());
        }
        if next
            .regions
            .iter()
            .any(|r| self.containers.contains_key(&r.id) && r.kind != RegionKind::Container)
        {
            return Err("history:container-identity-changed".into());
        }
        Ok(())
    }

    pub fn observe(&mut self, world: &WorldSnapshot, now_ms: u64) {
        let Some(scene) = world.semantic.as_ref() else {
            return;
        };
        self.floor = Some(scene.clone());
        if !scene.coverage_known
            || scene.confidence < 0.9
            || scene.observed_ms > now_ms
            || now_ms - scene.observed_ms > 200
        {
            return;
        }
        if scene.item != ItemKind::Unknown {
            self.items.insert(scene.item_id.clone(), scene.item);
        }
        for region in &scene.regions {
            if region.kind != RegionKind::Unknown {
                self.region_kinds.insert(region.id.clone(), region.kind);
            }
        }
        for observed in scene
            .regions
            .iter()
            .filter(|r| r.kind == RegionKind::Container)
        {
            let mut retained = observed.clone();
            if let Some(previous) = self.containers.get(&observed.id) {
                for kind in &previous.contents {
                    if !retained.contents.contains(kind) {
                        retained.contents.push(*kind);
                    }
                }
                // Keep known contaminants; sensor uncertainty itself is not a
                // material effect. Fresh trusted coverage may resolve occlusion.
                retained.contents_known = observed.contents_known;
            }
            retained.contents.retain(|kind| *kind != ItemKind::Unknown);
            retained.contents.sort_by_key(|kind| *kind as u8);
            retained.contents.dedup();
            self.containers.insert(observed.id.clone(), retained);
        }
    }

    pub fn regions(&self) -> Vec<Region> {
        self.containers.values().cloned().collect()
    }

    pub fn check_admission(&self, proposal: &ActionProposal) -> Result<(), String> {
        let Some(binding) = proposal.semantic.as_ref() else {
            return Ok(());
        };
        if self.pending.is_some() {
            return Err("history:unresolved-motion".into());
        }
        if binding.task_revision < self.step_epoch {
            return Err("history:stale-task-revision".into());
        }
        if binding.task_revision <= self.blocked_epoch {
            return Err("history:interrupted-task-revision".into());
        }
        if binding.task_revision == self.step_epoch {
            if self
                .steps
                .contains_key(&step_key(&binding.task_id, &binding.step_id))
            {
                return Err("history:consumed-step".into());
            }
            if self.steps.len() >= MAX_STEPS {
                return Err("history:step-capacity".into());
            }
        }
        Ok(())
    }

    pub fn reserve(&mut self, proposal: &ActionProposal, approved: &ActionKind, now_ms: u64) {
        let Some(binding) = proposal.semantic.as_ref() else {
            return;
        };
        let ActionKind::JointTrajectory { points, .. } = approved else {
            return;
        };
        if binding.task_revision > self.step_epoch {
            // Only the trusted observer can create a newer task epoch. Old
            // bindings remain denied by the durable observation floor.
            self.steps.clear();
            self.step_epoch = binding.task_revision;
        }
        let mut emitted = proposal.clone();
        emitted.action = approved.clone();
        let command_sha256 = hex::encode(Sha256::digest(
            serde_json::to_vec(&emitted).expect("validated finite trajectory"),
        ));
        let key = step_key(&binding.task_id, &binding.step_id);
        self.steps.insert(
            key.clone(),
            MotionRecord {
                task_revision: binding.task_revision,
                task_id: binding.task_id.clone(),
                step_id: binding.step_id.clone(),
                item_id: binding.item_id.clone(),
                proposal_id: proposal.id,
                source: proposal.source,
                command_sha256,
                started_ms: now_ms,
                end_ms: now_ms.saturating_add(points.last().unwrap().time_from_start_ms),
                final_positions: points.last().unwrap().positions.clone(),
                outcome: MotionOutcome::Reserved,
                stopped_ms: None,
            },
        );
        self.pending = Some(key);
        self.settle_samples = 0;
        self.settle_stamp = 0;
    }

    pub fn interrupt(&mut self) {
        if let Some(record) = self
            .pending
            .as_ref()
            .and_then(|key| self.steps.get_mut(key))
        {
            record.outcome = MotionOutcome::Interrupted;
            self.blocked_epoch = self.blocked_epoch.max(record.task_revision);
            self.settle_samples = 0;
        }
    }

    pub fn retire(&mut self, expected_expiry: bool) {
        if expected_expiry {
            if let Some(record) = self
                .pending
                .as_ref()
                .and_then(|key| self.steps.get_mut(key))
            {
                record.outcome = MotionOutcome::AwaitingStop;
                self.settle_stamp = 0;
                self.settle_samples = 0;
            }
        } else {
            self.interrupt();
        }
    }

    pub fn observe_stop(
        &mut self,
        world: &WorldSnapshot,
        now_ms: u64,
        cancelling: bool,
        policy: &Policy,
    ) {
        let Some(key) = self.pending.clone() else {
            return;
        };
        let record = self.steps.get_mut(&key).unwrap();
        if record.outcome == MotionOutcome::Reserved || cancelling {
            return;
        }
        let Some(scene) = world.semantic.as_ref() else {
            self.settle_samples = 0;
            return;
        };
        let Some(joints) = world.robot.joints.as_ref() else {
            self.settle_samples = 0;
            return;
        };
        let fresh = world.stamp_ms <= now_ms
            && now_ms - world.stamp_ms <= 200
            && scene.observed_ms <= now_ms
            && now_ms - scene.observed_ms <= 200
            && scene.coverage_known
            && scene.confidence >= 0.9
            && world.confidence >= 0.9;
        if !fresh
            || world.stamp_ms <= record.started_ms
            || world.stamp_ms <= self.settle_stamp
            || joints.len() != 4
            || world
                .robot
                .twist
                .is_none_or(|t| t.linear.abs() > 0.01 || t.angular.abs() > 0.01)
        {
            self.settle_samples = 0;
            return;
        }
        self.settle_stamp = world.stamp_ms;
        let arm = policy.arm.as_ref().unwrap();
        let stopped = joints.iter().zip(&arm.joints).all(|(j, p)| {
            j.name == p.name
                && j.velocity.abs() <= 0.01
                && j.position >= p.min_position
                && j.position <= p.max_position
        });
        if !stopped {
            self.settle_samples = 0;
            return;
        }
        self.settle_samples += 1;
        if self.settle_samples < 2 {
            return;
        }
        let endpoint = world.stamp_ms >= record.end_ms
            && scene.task_revision == record.task_revision
            && scene.task_id == record.task_id
            && scene.step_id == record.step_id
            && scene.item_id == record.item_id
            && joints
                .iter()
                .zip(&record.final_positions)
                .all(|(j, p)| (j.position - p).abs() <= arm.max_tracking_error);
        if record.outcome == MotionOutcome::AwaitingStop && endpoint {
            record.outcome = MotionOutcome::MotionSettled;
        } else {
            record.outcome = MotionOutcome::Interrupted;
            self.blocked_epoch = self.blocked_epoch.max(record.task_revision);
        }
        record.stopped_ms = Some(world.stamp_ms);
        self.pending = None;
    }
}
