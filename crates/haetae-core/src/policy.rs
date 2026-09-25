use std::collections::HashSet;
use std::fmt;

use serde::{Deserialize, Serialize};

use crate::geom::Rect;
use crate::proposal::Source;
use crate::world::HumanClass;

/// Everything the gate enforces. Unknown fields are rejected so that a typo
/// can never silently weaken a policy.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub envelope: Envelope,
    /// Proposals from any other source are denied (except `Stop`). Required:
    /// forgetting it must not mean "allow everyone".
    pub allowed_sources: Vec<Source>,
    #[serde(default)]
    pub freshness: Freshness,
    #[serde(default)]
    pub zones: Vec<Zone>,
    #[serde(default)]
    pub rules: Vec<Rule>,
}

/// Upper bound for every freshness budget: one minute.
pub const MAX_FRESHNESS_BUDGET_MS: u64 = 60_000;

/// How old (or how far in the future) inputs may be, relative to the
/// runtime clock passed to [`crate::Gate::judge_at`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Freshness {
    /// Maximum age of the world snapshot. Older: `stale:world`.
    pub world_max_age_ms: u64,
    /// Maximum age of the proposal's claimed timestamp. Older: `stale:proposal`.
    /// The claim is untrusted, so this only stops naive replays; the runtime's
    /// duplicate-id check covers the rest.
    pub proposal_max_age_ms: u64,
    /// Allowed clock skew into the future. Further: `invalid:timestamp`.
    pub future_tolerance_ms: u64,
}

impl Default for Freshness {
    fn default() -> Self {
        Freshness {
            world_max_age_ms: 500,
            proposal_max_age_ms: 2000,
            future_tolerance_ms: 100,
        }
    }
}

/// Hard limits that apply regardless of rules.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope {
    pub max_speed: f64,
    pub workspace: Rect,
}

/// A named area with restrictions. In W3 these arrive as signed `geumpyo` files.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Zone {
    pub id: String,
    pub area: Rect,
    #[serde(default)]
    pub no_entry: bool,
    #[serde(default)]
    pub speed_limit: Option<f64>,
}

/// A semantic rule: when every clause in `when` holds, apply `then`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    pub id: String,
    pub when: Condition,
    pub then: Effect,
}

/// Clauses are AND-ed. At least one clause is required.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Condition {
    /// The robot holds, or the proposal grasps, one of these objects.
    #[serde(default)]
    pub object_any: Option<Vec<String>>,
    /// A human (optionally of a class) is within `distance` of the swept path.
    #[serde(default)]
    pub human_within: Option<HumanWithin>,
    /// Safety-perception confidence is below this value.
    #[serde(default)]
    pub confidence_below: Option<f64>,
    /// The proposal comes from one of these sources.
    #[serde(default)]
    pub source_in: Option<Vec<Source>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HumanWithin {
    #[serde(default)]
    pub class: Option<HumanClass>,
    pub distance: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub enum Effect {
    Bul,
    Jeol { max_speed: f64 },
}

#[derive(Debug, Clone, PartialEq)]
pub enum PolicyError {
    Parse(String),
    Invalid(String),
}

impl fmt::Display for PolicyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PolicyError::Parse(m) => write!(f, "policy parse error: {m}"),
            PolicyError::Invalid(m) => write!(f, "invalid policy: {m}"),
        }
    }
}

impl std::error::Error for PolicyError {}

fn non_negative(v: f64) -> bool {
    v.is_finite() && v >= 0.0
}

impl Policy {
    pub fn from_json(s: &str) -> Result<Policy, PolicyError> {
        let policy: Policy =
            serde_json::from_str(s).map_err(|e| PolicyError::Parse(e.to_string()))?;
        policy.validate()?;
        Ok(policy)
    }

    pub fn validate(&self) -> Result<(), PolicyError> {
        let invalid = |m: String| Err(PolicyError::Invalid(m));
        if !(self.envelope.max_speed.is_finite() && self.envelope.max_speed > 0.0) {
            return invalid("envelope.max_speed must be a positive number".into());
        }
        if !self.envelope.workspace.is_valid() {
            return invalid("envelope.workspace is not a valid rectangle".into());
        }
        let f = &self.freshness;
        let budgets = [
            f.world_max_age_ms,
            f.proposal_max_age_ms,
            f.future_tolerance_ms,
        ];
        if f.world_max_age_ms == 0 || f.proposal_max_age_ms == 0 {
            return invalid("freshness budgets must be positive".into());
        }
        // A huge budget would saturate the clock arithmetic and switch the
        // checks off, so it is a policy error, not a lenient setting.
        if budgets.iter().any(|&b| b > MAX_FRESHNESS_BUDGET_MS) {
            return invalid(format!(
                "freshness budgets must not exceed {MAX_FRESHNESS_BUDGET_MS} ms"
            ));
        }
        if f.future_tolerance_ms > f.world_max_age_ms {
            return invalid("future_tolerance_ms must not exceed world_max_age_ms".into());
        }

        let mut ids = HashSet::new();
        let mut check_id = |id: &str| -> Result<(), PolicyError> {
            if id.is_empty() || id.contains(':') {
                return invalid(format!(
                    "id `{id}` must be non-empty and must not contain `:` (reserved for built-in checks)"
                ));
            }
            if !ids.insert(id.to_string()) {
                return invalid(format!("duplicate id `{id}`"));
            }
            Ok(())
        };
        for zone in &self.zones {
            check_id(&zone.id)?;
            if !zone.no_entry && zone.speed_limit.is_none() {
                return invalid(format!(
                    "zone `{}` restricts nothing: set no_entry or speed_limit",
                    zone.id
                ));
            }
            if !zone.area.is_valid() {
                return invalid(format!("zone `{}` has an invalid area", zone.id));
            }
            if zone.speed_limit.is_some_and(|v| !non_negative(v)) {
                return invalid(format!("zone `{}` has an invalid speed_limit", zone.id));
            }
        }
        for rule in &self.rules {
            check_id(&rule.id)?;
            let c = &rule.when;
            if *c == Condition::default() {
                return invalid(format!("rule `{}` has an empty condition", rule.id));
            }
            if c.object_any.as_ref().is_some_and(Vec::is_empty)
                || c.source_in.as_ref().is_some_and(Vec::is_empty)
            {
                return invalid(format!(
                    "rule `{}` has an empty list, so it can never match",
                    rule.id
                ));
            }
            if c.human_within
                .as_ref()
                .is_some_and(|h| !non_negative(h.distance))
            {
                return invalid(format!(
                    "rule `{}` has an invalid human_within.distance",
                    rule.id
                ));
            }
            if c.confidence_below
                .is_some_and(|v| !(0.0..=1.0).contains(&v))
            {
                return invalid(format!(
                    "rule `{}` has confidence_below outside 0..=1",
                    rule.id
                ));
            }
            if let Effect::Jeol { max_speed } = rule.then {
                if !non_negative(max_speed) {
                    return invalid(format!("rule `{}` has an invalid jeol.max_speed", rule.id));
                }
                if max_speed >= self.envelope.max_speed {
                    return invalid(format!(
                        "rule `{}` jeol.max_speed is not below envelope.max_speed, so it never binds",
                        rule.id
                    ));
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BASE: &str = r#"{"allowed_sources":["planner"],"envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}}"#;

    fn with(extra: &str) -> String {
        format!("{BASE},{extra}}}")
    }

    #[test]
    fn minimal_policy_parses() {
        assert!(Policy::from_json(&format!("{BASE}}}")).is_ok());
    }

    #[test]
    fn typo_in_condition_is_rejected() {
        let s = with(r#""rules":[{"id":"r","when":{"human_withn":{"distance":1}},"then":"bul"}]"#);
        assert!(matches!(Policy::from_json(&s), Err(PolicyError::Parse(_))));
    }

    #[test]
    fn empty_condition_is_rejected() {
        let s = with(r#""rules":[{"id":"r","when":{},"then":"bul"}]"#);
        assert!(matches!(
            Policy::from_json(&s),
            Err(PolicyError::Invalid(_))
        ));
    }

    #[test]
    fn duplicate_ids_are_rejected() {
        let zone = r#"{"id":"a","area":{"min":{"x":0,"y":0},"max":{"x":1,"y":1}},"no_entry":true}"#;
        let s = with(&format!(r#""zones":[{zone},{zone}]"#));
        assert!(matches!(
            Policy::from_json(&s),
            Err(PolicyError::Invalid(_))
        ));
    }

    #[test]
    fn missing_allowed_sources_is_rejected() {
        let s = r#"{"envelope":{"max_speed":1.0,"workspace":{"min":{"x":0,"y":0},"max":{"x":10,"y":10}}}}"#;
        assert!(matches!(Policy::from_json(s), Err(PolicyError::Parse(_))));
    }

    #[test]
    fn dead_rules_and_zones_are_rejected() {
        let cases = [
            r#""rules":[{"id":"r","when":{"object_any":[],"confidence_below":0.5},"then":"bul"}]"#,
            r#""rules":[{"id":"r","when":{"source_in":[]},"then":"bul"}]"#,
            r#""rules":[{"id":"r","when":{"confidence_below":0.5},"then":{"jeol":{"max_speed":2.0}}}]"#,
            r#""zones":[{"id":"z","area":{"min":{"x":0,"y":0},"max":{"x":1,"y":1}}}]"#,
        ];
        for extra in cases {
            assert!(
                matches!(
                    Policy::from_json(&with(extra)),
                    Err(PolicyError::Invalid(_))
                ),
                "{extra}"
            );
        }
    }

    #[test]
    fn ids_cannot_shadow_builtin_checks() {
        let s = with(
            r#""rules":[{"id":"source:not-allowed","when":{"confidence_below":0.5},"then":"bul"}]"#,
        );
        assert!(matches!(
            Policy::from_json(&s),
            Err(PolicyError::Invalid(_))
        ));
    }

    #[test]
    fn jeol_effect_parses() {
        let s = with(
            r#""rules":[{"id":"r","when":{"confidence_below":0.5},"then":{"jeol":{"max_speed":0.2}}}]"#,
        );
        let p = Policy::from_json(&s).unwrap();
        assert_eq!(p.rules[0].then, Effect::Jeol { max_speed: 0.2 });
    }
}
