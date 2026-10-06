//! Fail-closed command execution state machine. A transport must publish every
//! `Step.cmd` and must stop its actuator independently if this process dies.

pub mod auth;
mod history;
mod state;
pub use state::{set as set_state, show as show_state};

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::error::Error;
use std::path::PathBuf;

use auth::Role;
use haetae_core::household::{self, SemanticSnapshot};
use haetae_core::{
    ActionKind, ActionProposal, JointWaypoint, Mode, Policy, Source, Twist2, Verdict,
};
use haetae_runtime::{Fault, Inbound, Outcome, Runtime, RuntimeConfig};
use history::History;
pub use history::HistoryStatus;
use serde::Serialize;
use state::StateStore;

pub struct EnforcerConfig {
    pub runtime: RuntimeConfig,
    pub state_path: Option<PathBuf>,
    pub tick_ms: u64,
}

impl Default for EnforcerConfig {
    fn default() -> Self {
        Self {
            runtime: RuntimeConfig::default(),
            state_path: None,
            tick_ms: 50,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    Startup,
    NoWorld,
    StaleWorld,
    NoCommand,
    Expired,
    Denied,
    Revoked,
    Unarmed,
    RecorderFault,
    StateFault,
    ArmSettling,
    Mode(Mode),
}

#[derive(Debug, Clone, Serialize)]
pub struct Status {
    pub mode: Mode,
    pub stop: Option<StopReason>,
    pub armed: BTreeSet<Source>,
    pub active: Option<ActionProposal>,
    pub active_expires_ms: Option<u64>,
    pub suppressed: u64,
    pub world_age_ms: Option<u64>,
    /// Latest context actually accepted by the trusted world channel. These
    /// are observation acknowledgments, not motion authorization tokens.
    pub semantic_revision: Option<u64>,
    pub semantic_task_revision: Option<u64>,
    pub recorder_ok: bool,
    pub state_ok: bool,
    pub arm_cancelling: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub history: Option<HistoryStatus>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Step {
    pub cmd: Twist2,
    pub arm: Option<ArmOutput>,
    pub publish_now: bool,
    pub stop: Option<StopReason>,
    pub outcome: Option<Outcome>,
    pub status: Status,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ArmOutput {
    Execute { points: Vec<JointWaypoint> },
    Cancel,
}

struct Active {
    proposal: ActionProposal,
    approved: Twist2,
    expires_ms: u64,
}

struct ActiveArm {
    proposal: ActionProposal,
    /// Trusted facts captured when the exact trajectory was admitted. New
    /// observations can refresh facts, but cannot replace its bound identity.
    semantic: Option<SemanticSnapshot>,
    started_ms: u64,
    expires_ms: u64,
}

pub struct Enforcer {
    runtime: Runtime,
    state: Option<StateStore>,
    active: Option<Active>,
    active_arm: Option<ActiveArm>,
    arm_cancel_pending: bool,
    arm_cancelling: bool,
    cancel_since_ms: u64,
    last_settle_stamp: u64,
    settle_samples: u8,
    last_now_ms: u64,
    armed: BTreeSet<Source>,
    suppressed: u64,
    state_ok: bool,
    ever_armed: bool,
    last_stop: Option<StopReason>,
    last_cmd: Twist2,
    tick_ms: u64,
    auth_checkpoint: Option<(u64, BTreeMap<Role, u64>)>,
    history: Option<History>,
}

impl Enforcer {
    pub fn open(
        policy: Policy,
        mut cfg: EnforcerConfig,
        now_ms: u64,
    ) -> Result<Self, Box<dyn Error>> {
        if cfg.tick_ms == 0 || cfg.tick_ms > 1000 {
            return Err("tick_ms must be 1..=1000".into());
        }
        let state = match cfg.state_path.take() {
            Some(path) => Some(StateStore::open(path, now_ms, &policy)?),
            None => None,
        };
        if let Some(s) = &state {
            cfg.runtime.start_mode = s.mode();
        }
        cfg.runtime.defer_seal = true;
        let history = state
            .as_ref()
            .and_then(StateStore::history)
            .or_else(|| History::new(&policy));
        let mut runtime = Runtime::new(policy, None, cfg.runtime)?;
        if let Some(h) = &history {
            runtime.set_container_history(h.regions());
        }
        let recovery_stop = history.as_ref().is_some_and(|h| h.status().motion_pending);
        Ok(Self {
            runtime,
            state,
            active: None,
            active_arm: None,
            arm_cancel_pending: recovery_stop,
            arm_cancelling: recovery_stop,
            cancel_since_ms: if recovery_stop { 0 } else { now_ms },
            last_settle_stamp: 0,
            settle_samples: 0,
            last_now_ms: now_ms,
            armed: BTreeSet::new(),
            suppressed: 0,
            state_ok: true,
            ever_armed: false,
            last_stop: Some(StopReason::Startup),
            last_cmd: zero(),
            tick_ms: cfg.tick_ms,
            auth_checkpoint: None,
            history,
        })
    }

    pub fn tick_ms(&self) -> u64 {
        self.tick_ms
    }

    pub fn auth_checkpoint(&self) -> (u64, BTreeMap<Role, u64>) {
        self.state
            .as_ref()
            .map_or((0, BTreeMap::new()), StateStore::checkpoint)
    }

    pub fn set_auth_checkpoint(&mut self, epoch: u64, counters: BTreeMap<Role, u64>) {
        self.auth_checkpoint = Some((epoch, counters));
    }

    pub fn handle(&mut self, input: Inbound, now_ms: u64) -> Step {
        self.last_now_ms = now_ms;
        let mut forced = None;
        let mut arm_execute = None;
        let outcome = match input {
            Inbound::Proposal(p)
                if p.action == ActionKind::Stop
                    || matches!(p.action, ActionKind::Velocity { linear, angular, .. } if linear == 0.0 && angular == 0.0) =>
            {
                let source = p.source;
                self.clear_active();
                if !self.arm_cancelling {
                    self.armed.insert(source);
                    self.ever_armed = true;
                }
                let mut stop = p;
                stop.action = ActionKind::Stop;
                self.runtime.handle(Inbound::Proposal(stop), now_ms).ok()
            }
            Inbound::Proposal(p) => {
                if self.arm_cancelling
                    || self.active_arm.is_some()
                    || !self.armed.contains(&p.source)
                {
                    self.clear_active();
                    self.armed.clear();
                    self.suppressed += 1;
                    forced = Some(StopReason::Unarmed);
                    Some(Outcome::Rejected {
                        error: "source must send a zero command to arm".into(),
                    })
                } else if !matches!(
                    p.action,
                    ActionKind::Velocity { .. } | ActionKind::JointTrajectory { .. }
                ) {
                    self.clear_active();
                    self.armed.clear();
                    forced = Some(StopReason::Denied);
                    Some(Outcome::Rejected {
                        error: "no monitored actuator adapter for this action".into(),
                    })
                } else if let Some(error) = self
                    .history
                    .as_ref()
                    .and_then(|h| h.check_admission(&p).err())
                {
                    self.clear_active();
                    self.armed.clear();
                    forced = Some(StopReason::Denied);
                    Some(self.runtime.reject_input(error, b"", now_ms))
                } else {
                    let out = self
                        .runtime
                        .handle(Inbound::Proposal(p.clone()), now_ms)
                        .ok();
                    match &out {
                        Some(Outcome::Decision(d)) if d.verdict != Verdict::Bul => {
                            if let Some(ActionKind::Velocity {
                                linear, angular, ..
                            }) = &d.action
                            {
                                self.clear_active();
                                self.active = Some(Active {
                                    proposal: p,
                                    approved: Twist2 {
                                        linear: *linear,
                                        angular: *angular,
                                    },
                                    expires_ms: d.expires_ms.unwrap_or(now_ms),
                                });
                            } else if let Some(ActionKind::JointTrajectory { points, .. }) =
                                &d.action
                            {
                                self.clear_active();
                                if let Some(history) = &mut self.history {
                                    history.reserve(&p, d.action.as_ref().unwrap(), now_ms);
                                }
                                arm_execute = Some(points.clone());
                                self.active_arm = Some(ActiveArm {
                                    proposal: p,
                                    semantic: self
                                        .runtime
                                        .effective_world()
                                        .and_then(|world| world.semantic),
                                    started_ms: now_ms,
                                    expires_ms: d.expires_ms.unwrap_or(now_ms),
                                });
                            }
                        }
                        _ => {
                            self.clear_active();
                            self.armed.clear();
                            forced = Some(StopReason::Denied);
                        }
                    }
                    out
                }
            }
            Inbound::World(w) => {
                if let Some(error) = self
                    .history
                    .as_ref()
                    .and_then(|h| h.check_world(&w, now_ms).err())
                {
                    return self.reject(error, b"", now_ms);
                }
                let observed = w.clone();
                let out = self.runtime.handle(Inbound::World(w), now_ms).ok();
                if matches!(out, Some(Outcome::WorldUpdated { .. })) {
                    if let Some(h) = &mut self.history {
                        h.observe(&observed, now_ms);
                        self.runtime.set_container_history(h.regions());
                    }
                    self.update_arm_settle();
                    self.rejudge(now_ms, &mut forced);
                    if self.active_arm.is_none() {
                        if let Some(h) = &mut self.history {
                            h.observe_stop(
                                &observed,
                                now_ms,
                                self.arm_cancelling,
                                self.runtime.policy(),
                            );
                        }
                    }
                } else {
                    self.clear_active();
                    self.armed.clear();
                    forced = Some(StopReason::Denied);
                }
                out
            }
            Inbound::Fault(f) => {
                let out = self.runtime.handle(Inbound::Fault(f), now_ms).ok();
                if out.is_none() || self.runtime.mode().stop_only() {
                    self.clear_active();
                    self.armed.clear();
                } else {
                    self.rejudge(now_ms, &mut forced);
                }
                out
            }
        };
        self.output(now_ms, outcome, forced, arm_execute)
    }

    pub fn handle_bytes(&mut self, bytes: &[u8], now_ms: u64) -> Step {
        match Inbound::from_json(bytes) {
            Ok(input) => self.handle(input, now_ms),
            Err(error) => self.reject(error, bytes, now_ms),
        }
    }

    pub fn reject(&mut self, error: String, bytes: &[u8], now_ms: u64) -> Step {
        self.last_now_ms = now_ms;
        self.clear_active();
        self.armed.clear();
        let outcome = self.runtime.reject_input(error, bytes, now_ms);
        self.output(now_ms, Some(outcome), Some(StopReason::Denied), None)
    }

    pub fn tick(&mut self, now_ms: u64) -> Step {
        self.last_now_ms = now_ms;
        let mut forced = None;
        self.rejudge(now_ms, &mut forced);
        self.output(now_ms, None, forced, None)
    }

    fn rejudge(&mut self, now_ms: u64, forced: &mut Option<StopReason>) {
        // Ordinary lease expiry belongs to output(), including cancellation.
        // Do not turn it into a semantic revocation at the exact TTL boundary.
        if self
            .active_arm
            .as_ref()
            .is_some_and(|active| now_ms < active.expires_ms)
            && !self.runtime.mode().stop_only()
            && self
                .runtime
                .world_age_ms(now_ms)
                .is_some_and(|age| age <= self.runtime.world_max_age_ms())
        {
            if let Err(reason) = self.monitor_arm(now_ms) {
                self.runtime.revoke_record(&reason, now_ms);
                self.clear_active();
                self.armed.clear();
                *forced = Some(StopReason::Revoked);
            }
        }
        let Some(active) = &mut self.active else {
            return;
        };
        if now_ms >= active.expires_ms {
            return;
        }
        if self.runtime.mode().stop_only()
            || self
                .runtime
                .world_age_ms(now_ms)
                .is_none_or(|age| age > self.runtime.world_max_age_ms())
        {
            return;
        }
        let d = self.runtime.rejudge(&active.proposal, now_ms);
        match d.action {
            Some(ActionKind::Velocity {
                linear, angular, ..
            }) if d.verdict != Verdict::Bul => {
                // An improving world cannot accelerate an already approved command.
                if linear.abs() < active.approved.linear.abs()
                    || angular.abs() < active.approved.angular.abs()
                {
                    let linear_factor = if active.approved.linear == 0.0 {
                        1.0
                    } else {
                        linear.abs() / active.approved.linear.abs()
                    };
                    let angular_factor = if active.approved.angular == 0.0 {
                        1.0
                    } else {
                        angular.abs() / active.approved.angular.abs()
                    };
                    let factor = linear_factor.min(angular_factor).min(1.0);
                    active.approved = Twist2 {
                        linear: active.approved.linear * factor,
                        angular: active.approved.angular * factor,
                    };
                }
            }
            _ => {
                self.clear_active();
                self.armed.clear();
                *forced = Some(StopReason::Revoked);
            }
        }
    }

    fn monitor_arm(&self, now_ms: u64) -> Result<(), String> {
        if self.runtime.mode() != Mode::Normal {
            return Err("arm:mode-changed".into());
        }
        let active = self.active_arm.as_ref().ok_or("arm:no-active")?;
        let effective_world = self.runtime.effective_world().ok_or("arm:no-world")?;
        let world = &effective_world;
        let arm = self.runtime.policy().arm.as_ref().ok_or("arm:no-policy")?;
        if self.runtime.policy().household.is_some() {
            let original = active
                .semantic
                .as_ref()
                .ok_or("household:missing-admission-snapshot")?;
            household::recheck_active(
                self.runtime.policy(),
                &active.proposal,
                original,
                world,
                active.started_ms,
                now_ms,
            )?;
        }
        if !world.humans.is_empty() || world.confidence < arm.min_confidence {
            return Err("arm:world-changed".into());
        }
        let measured = world.robot.joints.as_ref().ok_or("arm:no-joints")?;
        let ActionKind::JointTrajectory { points, .. } = &active.proposal.action else {
            return Err("arm:invalid-action".into());
        };
        if measured.len() != arm.joints.len() {
            return Err("arm:joint-count".into());
        }
        if world.stamp_ms < active.started_ms {
            return Ok(());
        }
        let elapsed = world.stamp_ms.saturating_sub(active.started_ms);
        let mut expected = &points[0].positions;
        let mut next = expected;
        let mut fraction = 0.0;
        for pair in points.windows(2) {
            if elapsed <= pair[1].time_from_start_ms {
                expected = &pair[0].positions;
                next = &pair[1].positions;
                let span = pair[1].time_from_start_ms - pair[0].time_from_start_ms;
                fraction = elapsed.saturating_sub(pair[0].time_from_start_ms) as f64 / span as f64;
                break;
            }
            expected = &pair[1].positions;
            next = expected;
        }
        for (i, (sample, limit)) in measured.iter().zip(&arm.joints).enumerate() {
            let desired = expected[i] + (next[i] - expected[i]) * fraction;
            if sample.name != limit.name
                || sample.position < limit.min_position
                || sample.position > limit.max_position
                || sample.velocity.abs() > limit.max_velocity
                || (sample.position - desired).abs() > arm.max_tracking_error
            {
                return Err("arm:tracking".into());
            }
        }
        Ok(())
    }

    fn clear_active(&mut self) {
        self.clear_active_with_completion(false);
    }

    fn clear_active_with_completion(&mut self, expected_expiry: bool) {
        if let Some(h) = &mut self.history {
            h.retire(expected_expiry);
        }
        self.active = None;
        if self.active_arm.take().is_some() {
            self.arm_cancel_pending = true;
            self.arm_cancelling = true;
            self.cancel_since_ms = self.last_now_ms;
            self.settle_samples = 0;
            self.armed.clear();
        }
    }

    fn update_arm_settle(&mut self) {
        if !self.arm_cancelling {
            return;
        }
        let Some(world) = self.runtime.world() else {
            return;
        };
        let Some(joints) = &world.robot.joints else {
            return;
        };
        if world.stamp_ms <= self.cancel_since_ms
            || world.stamp_ms <= self.last_settle_stamp
            || joints.is_empty()
        {
            return;
        }
        self.last_settle_stamp = world.stamp_ms;
        if joints.iter().all(|j| j.velocity.abs() <= 0.01) {
            self.settle_samples += 1;
            if self.settle_samples >= 2 {
                self.arm_cancelling = false;
            }
        } else {
            self.settle_samples = 0;
        }
    }

    fn output(
        &mut self,
        now_ms: u64,
        outcome: Option<Outcome>,
        forced: Option<StopReason>,
        arm_execute: Option<Vec<JointWaypoint>>,
    ) -> Step {
        let world_age = self.runtime.world_age_ms(now_ms);
        let reason = if !self.state_ok {
            Some(StopReason::StateFault)
        } else if self.runtime.recorder_fault().is_some() {
            Some(StopReason::RecorderFault)
        } else if self.runtime.mode().stop_only() {
            Some(StopReason::Mode(self.runtime.mode()))
        } else if self.arm_cancelling {
            Some(StopReason::ArmSettling)
        } else if world_age.is_none() {
            Some(StopReason::NoWorld)
        } else if world_age.is_some_and(|age| age > self.runtime.world_max_age_ms()) {
            Some(StopReason::StaleWorld)
        } else if let Some(forced) = forced {
            Some(forced)
        } else if self.active.as_ref().is_some_and(|a| now_ms >= a.expires_ms)
            || self
                .active_arm
                .as_ref()
                .is_some_and(|a| now_ms >= a.expires_ms)
        {
            Some(StopReason::Expired)
        } else if self.active.is_none() && self.active_arm.is_none() {
            Some(
                if self.armed.is_empty()
                    && matches!(
                        self.last_stop.as_ref(),
                        Some(
                            StopReason::Denied
                                | StopReason::Revoked
                                | StopReason::Unarmed
                                | StopReason::StaleWorld
                        )
                    )
                {
                    self.last_stop.clone().expect("matched a stop reason")
                } else if self.ever_armed {
                    StopReason::NoCommand
                } else {
                    StopReason::Startup
                },
            )
        } else {
            None
        };
        if matches!(
            reason,
            Some(
                StopReason::StaleWorld
                    | StopReason::NoWorld
                    | StopReason::Mode(_)
                    | StopReason::RecorderFault
                    | StopReason::StateFault
            )
        ) {
            self.clear_active();
            self.armed.clear();
        }
        if matches!(reason, Some(StopReason::Expired)) {
            let expected = self.active_arm.as_ref().is_some_and(|active| {
                let ActionKind::JointTrajectory { points, .. } = &active.proposal.action else {
                    return false;
                };
                active.expires_ms
                    >= active
                        .proposal
                        .timestamp_ms
                        .saturating_add(points.last().unwrap().time_from_start_ms)
            });
            self.clear_active_with_completion(expected);
        }
        let cmd = if reason.is_none() {
            self.active.as_ref().map_or(zero(), |a| a.approved)
        } else {
            zero()
        };
        let arm = if self.arm_cancel_pending {
            self.arm_cancel_pending = false;
            Some(ArmOutput::Cancel)
        } else if reason.is_none() {
            arm_execute.map(|points| ArmOutput::Execute { points })
        } else {
            None
        };
        let publish_now = cmd != self.last_cmd
            || reason != self.last_stop
            || matches!(reason, Some(StopReason::Denied | StopReason::Revoked));
        if reason != self.last_stop {
            if let Some(r) = &reason {
                self.runtime.stop_record(&format!("{r:?}"), now_ms);
            }
        }
        self.last_stop = reason.clone();
        self.last_cmd = cmd;
        let status = Status {
            mode: self.runtime.mode(),
            stop: reason.clone(),
            armed: self.armed.clone(),
            active: self
                .active
                .as_ref()
                .map(|a| a.proposal.clone())
                .or_else(|| self.active_arm.as_ref().map(|a| a.proposal.clone())),
            active_expires_ms: self
                .active
                .as_ref()
                .map(|a| a.expires_ms)
                .or_else(|| self.active_arm.as_ref().map(|a| a.expires_ms)),
            suppressed: self.suppressed,
            world_age_ms: world_age,
            semantic_revision: self
                .runtime
                .world()
                .and_then(|world| world.semantic.as_ref().map(|scene| scene.revision)),
            semantic_task_revision: self
                .runtime
                .world()
                .and_then(|world| world.semantic.as_ref().map(|scene| scene.task_revision)),
            recorder_ok: self.runtime.recorder_fault().is_none(),
            state_ok: self.state_ok,
            arm_cancelling: self.arm_cancelling,
            history: self.history.as_ref().map(History::status),
        };
        Step {
            cmd,
            arm,
            publish_now,
            stop: reason,
            outcome,
            status,
        }
    }

    pub fn commit(&mut self, now_ms: u64) -> Result<(), Box<dyn Error>> {
        self.runtime.commit();
        if let Some(state) = &mut self.state {
            if let Err(e) = state.persist_checkpoint(
                self.runtime.mode(),
                self.auth_checkpoint.take(),
                &self.history,
                now_ms,
            ) {
                self.state_ok = false;
                return Err(Box::new(e));
            }
        }
        Ok(())
    }

    pub fn close(mut self, now_ms: u64) -> Result<(), Box<dyn Error>> {
        self.clear_active();
        self.commit(now_ms)?;
        self.runtime.close()?;
        if let Some(state) = self.state {
            state.close(now_ms)?;
        }
        Ok(())
    }
}

pub fn fault(code: impl Into<String>, mode: Mode, now_ms: u64) -> Inbound {
    Inbound::Fault(Fault {
        code: code.into(),
        raise_to: mode,
        timestamp_ms: now_ms,
    })
}

fn zero() -> Twist2 {
    Twist2 {
        linear: 0.0,
        angular: 0.0,
    }
}
