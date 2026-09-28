use serde::{Deserialize, Serialize};

use crate::arm::check_trajectory;
use crate::geom::{point_segment_distance, Point2};
use crate::mode::Mode;
use crate::policy::{Condition, Effect, Policy, PolicyError};
use crate::proposal::{ActionKind, ActionProposal};
use crate::velocity::swept_path;
use crate::verdict::Verdict;
use crate::world::WorldSnapshot;

/// The gate's answer to one proposal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Decision {
    pub proposal_id: u64,
    pub verdict: Verdict,
    /// Every check that matched, in evaluation order. Built-in checks are
    /// namespaced (`envelope:`, `zone:`, `mode:`, ...); rule ids cannot contain `:`.
    pub fired: Vec<String>,
    /// The action to execute: as proposed for `yun`, clamped for `jeol`, `None` for `bul`.
    pub action: Option<ActionKind>,
    /// Speed limit (m/s) the executor must apply to *all* motion for this
    /// action, arm included: the envelope maximum, or lower if a `jeol` check
    /// matched. Always set when an action is allowed, except for `stop`.
    pub speed_cap: Option<f64>,
    pub mode: Mode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_ms: Option<u64>,
}

/// Policy enforcement point between untrusted proposals and the actuators.
#[derive(Debug, Clone)]
pub struct Gate {
    policy: Policy,
    mode: Mode,
}

/// Accumulates check results; the strictest outcome wins.
struct Judgement {
    deny: bool,
    speed_cap: f64,
    fired: Vec<String>,
}

impl Judgement {
    fn deny(&mut self, check: impl Into<String>) {
        self.deny = true;
        self.fired.push(check.into());
    }

    fn cap(&mut self, check: impl Into<String>, max_speed: f64) {
        self.speed_cap = self.speed_cap.min(max_speed);
        self.fired.push(check.into());
    }
}

impl Gate {
    pub fn new(policy: Policy) -> Result<Gate, PolicyError> {
        Self::with_mode(policy, Mode::Normal)
    }

    pub fn with_mode(policy: Policy, mode: Mode) -> Result<Gate, PolicyError> {
        policy.validate()?;
        Ok(Gate { policy, mode })
    }

    pub fn policy(&self) -> &Policy {
        &self.policy
    }

    pub fn mode(&self) -> Mode {
        self.mode
    }

    /// Move up the mode ladder. Returns whether the mode changed; requests to
    /// move down are ignored.
    pub fn raise_mode(&mut self, to: Mode) -> bool {
        let changed = to > self.mode;
        self.mode = self.mode.max(to);
        changed
    }

    /// Operator reset back to `Normal`.
    // TODO(W3): require an operator-signed reset instead of a bare call.
    pub fn reset_mode(&mut self) {
        self.mode = Mode::Normal;
    }

    /// Judge one proposal against the policy using trusted world facts.
    ///
    /// `now_ms` comes from the runtime's trusted clock, in the same domain as
    /// `world.stamp_ms`. Never pass the proposal's own timestamp.
    pub fn judge_at(
        &self,
        proposal: &ActionProposal,
        world: &WorldSnapshot,
        now_ms: u64,
    ) -> Decision {
        let action = &proposal.action;
        let decide = |verdict, fired, action, speed_cap, expires_ms| Decision {
            proposal_id: proposal.id,
            verdict,
            fired,
            action,
            speed_cap,
            mode: self.mode,
            expires_ms,
        };
        let deny = |check: &str| decide(Verdict::Bul, vec![check.to_string()], None, None, None);
        let source_allowed = self.policy.allowed_sources.contains(&proposal.source);

        // Stopping is always allowed, whoever asks; an unvetted source is noted.
        if *action == ActionKind::Stop
            || matches!(action, ActionKind::Velocity { linear, angular, .. } if *linear == 0.0 && *angular == 0.0)
        {
            let fired = match source_allowed {
                true => Vec::new(),
                false => vec!["stop:unvetted-source".into()],
            };
            return decide(Verdict::Yun, fired, Some(ActionKind::Stop), None, None);
        }
        if !action.is_finite() {
            return deny("invalid:proposal");
        }
        if !world.is_valid() {
            return deny("invalid:world");
        }
        // Stale perception makes every later check meaningless, so these return early.
        let fresh = &self.policy.freshness;
        let future = |stamp: u64| stamp > now_ms.saturating_add(fresh.future_tolerance_ms);
        let age = |stamp: u64| now_ms.saturating_sub(stamp);
        if future(world.stamp_ms) || future(proposal.timestamp_ms) {
            return deny("invalid:timestamp");
        }
        if age(world.stamp_ms) > fresh.world_max_age_ms {
            return deny("stale:world");
        }
        if age(proposal.timestamp_ms) > fresh.proposal_max_age_ms {
            return deny("stale:proposal");
        }

        if let ActionKind::Velocity {
            linear,
            angular,
            ttl_ms,
        } = action
        {
            return self.judge_velocity(proposal, world, now_ms, *linear, *angular, *ttl_ms);
        }
        if let ActionKind::JointTrajectory { points, ttl_ms } = action {
            let check = if !self.policy.allowed_sources.contains(&proposal.source) {
                Err("source:not-allowed")
            } else if self.mode.stop_only() {
                Err("mode:arm-stop")
            } else if !world.humans.is_empty() {
                Err("arm:human-present")
            } else if let Some(arm) = &self.policy.arm {
                if world.confidence < arm.min_confidence {
                    Err("arm:low-confidence")
                } else {
                    check_trajectory(arm, world, points, *ttl_ms)
                }
            } else {
                Err("policy:no-arm")
            };
            if let Err(fired) = check {
                return deny(fired);
            }
            for rule in &self.policy.rules {
                if self.matches(
                    &rule.when,
                    proposal,
                    world,
                    &[world.robot.pose, world.robot.pose],
                    0.0,
                ) {
                    return match rule.then {
                        Effect::Bul => deny(&rule.id),
                        Effect::Jeol { .. } => deny("arm:cannot-clamp-rule"),
                    };
                }
            }
            return decide(
                Verdict::Yun,
                Vec::new(),
                Some(action.clone()),
                None,
                Some(now_ms.saturating_add(*ttl_ms)),
            );
        }

        let envelope = &self.policy.envelope;
        let mut j = Judgement {
            deny: false,
            speed_cap: f64::INFINITY,
            fired: Vec::new(),
        };
        // Only `Stop` has no path, and it was handled above.
        let Some((from, to)) = action.path(world.robot.pose) else {
            return deny("invalid:proposal");
        };

        if !source_allowed {
            j.deny("source:not-allowed");
        }

        if self.mode.stop_only() {
            j.deny(format!("mode:{}", mode_name(self.mode)));
        } else if self.mode == Mode::Caution {
            j.cap("mode:caution", envelope.max_speed * 0.5);
        }

        // The workspace is convex, so both endpoints inside means the whole path is.
        if !envelope.workspace.contains(from) {
            j.deny("envelope:pose");
        }
        if !envelope.workspace.contains(to) {
            j.deny("envelope:workspace");
        }
        if action.speed().is_some_and(|v| v > envelope.max_speed) {
            j.cap("envelope:max_speed", envelope.max_speed);
        }

        for zone in &self.policy.zones {
            if !zone.area.intersects_segment(from, to) {
                continue;
            }
            if zone.no_entry {
                j.deny(format!("zone:{}", zone.id));
            } else if let Some(limit) = zone.speed_limit {
                j.cap(format!("zone:{}", zone.id), limit);
            }
        }

        for rule in &self.policy.rules {
            if !self.matches(&rule.when, proposal, world, &[from, to], 0.0) {
                continue;
            }
            match rule.then {
                Effect::Bul => j.deny(rule.id.clone()),
                Effect::Jeol { max_speed } => j.cap(rule.id.clone(), max_speed),
            }
        }

        if j.deny {
            return decide(Verdict::Bul, j.fired, None, None, None);
        }
        // The envelope bounds every allowed action, including arm motion for
        // grasp and place, so the executor always receives a cap.
        if j.speed_cap.is_infinite() {
            let cap = Some(envelope.max_speed);
            return decide(Verdict::Yun, j.fired, Some(action.clone()), cap, None);
        }
        let limit = j.speed_cap.min(envelope.max_speed);
        let cap = Some(limit);
        match action.speed() {
            Some(v) if v > limit => decide(
                Verdict::Jeol,
                j.fired,
                Some(action.with_speed(limit)),
                cap,
                None,
            ),
            Some(_) => decide(Verdict::Yun, j.fired, Some(action.clone()), cap, None),
            // No speed field to clamp (grasp, place): the cap still binds, and
            // the executor enforces it through `speed_cap`.
            None => decide(Verdict::Jeol, j.fired, Some(action.clone()), cap, None),
        }
    }

    fn judge_velocity(
        &self,
        p: &ActionProposal,
        world: &WorldSnapshot,
        now_ms: u64,
        linear: f64,
        angular: f64,
        ttl_ms: u64,
    ) -> Decision {
        let denied = |check: String| Decision {
            proposal_id: p.id,
            verdict: Verdict::Bul,
            fired: vec![check],
            action: None,
            speed_cap: None,
            mode: self.mode,
            expires_ms: None,
        };
        let Some(base) = &self.policy.base else {
            return denied("policy:no-base".into());
        };
        if ttl_ms == 0 || ttl_ms > base.max_ttl_ms {
            return denied("envelope:ttl".into());
        }
        if linear < 0.0 && !base.allow_reverse {
            return denied("envelope:reverse".into());
        }
        let Some(yaw) = world.robot.yaw else {
            return denied("invalid:world".into());
        };
        if self.mode.stop_only() {
            return denied(format!("mode:{}", mode_name(self.mode)));
        }
        let mut fired = Vec::new();
        if !self.policy.allowed_sources.contains(&p.source) {
            return denied("source:not-allowed".into());
        }
        let mut factor = 1.0_f64;
        let mut cap = self.policy.envelope.max_speed;
        if self.mode == Mode::Caution {
            cap *= 0.5;
            fired.push("mode:caution".into());
        }
        if linear.abs() > cap {
            factor = factor.min(cap / linear.abs());
            fired.push("envelope:max_speed".into());
        }
        if angular.abs() > base.max_angular {
            factor = factor.min(base.max_angular / angular.abs());
            fired.push("envelope:max_angular".into());
        }
        let mut cmd_linear = linear * factor;
        let mut cmd_angular = angular * factor;
        // A zone or semantic cap scales the whole twist, preserving curvature.
        for _ in 0..2 {
            let path = match swept_path(world, yaw, cmd_linear, cmd_angular, ttl_ms, now_ms, base) {
                Ok(path) => path,
                Err(check) => return denied(check.into()),
            };
            if !path.inside(&self.policy.envelope.workspace) {
                return denied("envelope:workspace".into());
            }
            let mut tighter = cap;
            for zone in &self.policy.zones {
                if path.intersects(&zone.area) {
                    if zone.no_entry {
                        return denied(format!("zone:{}", zone.id));
                    }
                    if let Some(limit) = zone.speed_limit {
                        tighter = tighter.min(limit);
                        fired.push(format!("zone:{}", zone.id));
                    }
                }
            }
            for rule in &self.policy.rules {
                if self.matches(&rule.when, p, world, &path.points, path.inflation) {
                    match rule.then {
                        Effect::Bul => return denied(rule.id.clone()),
                        Effect::Jeol { max_speed } => {
                            tighter = tighter.min(max_speed);
                            fired.push(rule.id.clone());
                        }
                    }
                }
            }
            if cmd_linear.abs() <= tighter {
                break;
            }
            let next = tighter / cmd_linear.abs();
            cmd_linear *= next;
            cmd_angular *= next;
            cap = cap.min(tighter);
        }
        let verdict = if factor < 1.0
            || (cmd_linear - linear).abs() > f64::EPSILON
            || (cmd_angular - angular).abs() > f64::EPSILON
        {
            Verdict::Jeol
        } else {
            Verdict::Yun
        };
        Decision {
            proposal_id: p.id,
            verdict,
            fired,
            action: Some(ActionKind::Velocity {
                linear: cmd_linear,
                angular: cmd_angular,
                ttl_ms,
            }),
            speed_cap: Some(cap),
            mode: self.mode,
            expires_ms: Some(now_ms.saturating_add(ttl_ms)),
        }
    }

    fn matches(
        &self,
        c: &Condition,
        proposal: &ActionProposal,
        world: &WorldSnapshot,
        path: &[Point2],
        inflation: f64,
    ) -> bool {
        if let Some(objects) = &c.object_any {
            let grasping = match &proposal.action {
                ActionKind::Grasp { object, .. } => Some(object),
                _ => None,
            };
            let involved =
                |o: &String| world.robot.holding.as_ref() == Some(o) || grasping == Some(o);
            if !objects.iter().any(involved) {
                return false;
            }
        }
        if let Some(hw) = &c.human_within {
            let near = world.humans.iter().any(|h| {
                hw.class.is_none_or(|class| class == h.class)
                    && path.windows(2).any(|p| {
                        point_segment_distance(h.pos, p[0], p[1]) <= hw.distance + inflation
                    })
            });
            if !near {
                return false;
            }
        }
        if let Some(threshold) = c.confidence_below {
            if world.confidence >= threshold {
                return false;
            }
        }
        if let Some(sources) = &c.source_in {
            if !sources.contains(&proposal.source) {
                return false;
            }
        }
        true
    }
}

fn mode_name(mode: Mode) -> &'static str {
    match mode {
        Mode::Normal => "normal",
        Mode::Caution => "caution",
        Mode::Hold => "hold",
        Mode::SafePark => "safe_park",
        Mode::EStop => "estop",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The `mode:<name>` check uses the same names as the mode's JSON form.
    #[test]
    fn check_names_match_serialized_modes() {
        for mode in [
            Mode::Normal,
            Mode::Caution,
            Mode::Hold,
            Mode::SafePark,
            Mode::EStop,
        ] {
            assert_eq!(serde_json::to_value(mode).unwrap(), mode_name(mode));
        }
    }
}
