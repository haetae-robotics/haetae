use serde::{Deserialize, Serialize};

use crate::geom::{point_segment_distance, Point2};
use crate::mode::Mode;
use crate::policy::{Condition, Effect, Policy, PolicyError};
use crate::proposal::{ActionKind, ActionProposal};
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
    /// action, arm included. Set whenever a `jeol` check matched.
    pub speed_cap: Option<f64>,
    pub mode: Mode,
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
        policy.validate()?;
        Ok(Gate {
            policy,
            mode: Mode::Normal,
        })
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
    pub fn judge(&self, proposal: &ActionProposal, world: &WorldSnapshot) -> Decision {
        let action = &proposal.action;
        let decide = |verdict, fired, action, speed_cap| Decision {
            proposal_id: proposal.id,
            verdict,
            fired,
            action,
            speed_cap,
            mode: self.mode,
        };
        let deny = |check: &str| decide(Verdict::Bul, vec![check.to_string()], None, None);
        let source_allowed = self.policy.allowed_sources.contains(&proposal.source);

        // Stopping is always allowed, whoever asks; an unvetted source is noted.
        if *action == ActionKind::Stop {
            let fired = match source_allowed {
                true => Vec::new(),
                false => vec!["stop:unvetted-source".into()],
            };
            return decide(Verdict::Yun, fired, Some(ActionKind::Stop), None);
        }
        if !action.is_finite() {
            return deny("invalid:proposal");
        }
        if !world.is_valid() {
            return deny("invalid:world");
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
            if !self.matches(&rule.when, proposal, world, from, to) {
                continue;
            }
            match rule.then {
                Effect::Bul => j.deny(rule.id.clone()),
                Effect::Jeol { max_speed } => j.cap(rule.id.clone(), max_speed),
            }
        }

        if j.deny {
            return decide(Verdict::Bul, j.fired, None, None);
        }
        if j.speed_cap.is_infinite() {
            return decide(Verdict::Yun, j.fired, Some(action.clone()), None);
        }
        let cap = Some(j.speed_cap);
        match action.speed() {
            Some(v) if v > j.speed_cap => decide(
                Verdict::Jeol,
                j.fired,
                Some(action.with_speed(j.speed_cap)),
                cap,
            ),
            Some(_) => decide(Verdict::Yun, j.fired, Some(action.clone()), cap),
            // No speed field to clamp (grasp, place): the cap still binds, and
            // the executor enforces it through `speed_cap`.
            None => decide(Verdict::Jeol, j.fired, Some(action.clone()), cap),
        }
    }

    fn matches(
        &self,
        c: &Condition,
        proposal: &ActionProposal,
        world: &WorldSnapshot,
        from: Point2,
        to: Point2,
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
                    && point_segment_distance(h.pos, from, to) <= hw.distance
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
