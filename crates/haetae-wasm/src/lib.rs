//! WebAssembly bindings for the Haetae gate, used by the browser simulator
//! in `sim/`.
//!
//! The boundary is JSON strings in, JSON strings out, so every safety
//! decision is made by the same `haetae-core` code the tests cover; the
//! simulator only draws what the gate returns.

use haetae_core::{ActionProposal, Gate, Mode, Policy, WorldSnapshot};
use wasm_bindgen::prelude::*;

/// Largest integer a JS number holds exactly.
const MAX_SAFE_INTEGER: f64 = 9_007_199_254_740_991.0;

/// A gate plus its mode ladder, driven from JavaScript.
#[wasm_bindgen]
pub struct Sim {
    gate: Gate,
}

#[wasm_bindgen]
impl Sim {
    /// Build a gate from a policy JSON string. Throws with the policy error
    /// (parse or validation) if the policy does not load.
    #[wasm_bindgen(constructor)]
    pub fn new(policy_json: &str) -> Result<Sim, JsError> {
        Sim::from_policy(policy_json).map_err(|e| JsError::new(&e))
    }

    /// Judge a proposal against a world at `now_ms` (the simulator's trusted
    /// clock). Returns the `Decision` as JSON. Throws if either input is not
    /// valid JSON for its type.
    pub fn judge(
        &self,
        proposal_json: &str,
        world_json: &str,
        now_ms: f64,
    ) -> Result<String, JsError> {
        self.judge_json(proposal_json, world_json, now_ms)
            .map_err(|e| JsError::new(&e))
    }

    /// Raise the mode (`"caution"`, `"hold"`, `"safe_park"`, `"estop"`).
    /// Returns whether it changed; lower targets are ignored.
    pub fn raise_mode(&mut self, mode: &str) -> Result<bool, JsError> {
        self.raise(mode).map_err(|e| JsError::new(&e))
    }

    /// Operator reset back to `"normal"`.
    pub fn reset_mode(&mut self) {
        self.gate.reset_mode();
    }

    /// The current mode as its JSON name, e.g. `"caution"`.
    pub fn mode(&self) -> String {
        mode_name(self.gate.mode())
    }
}

impl Sim {
    fn from_policy(policy_json: &str) -> Result<Sim, String> {
        let policy = Policy::from_json(policy_json).map_err(|e| e.to_string())?;
        let gate = Gate::new(policy).map_err(|e| e.to_string())?;
        Ok(Sim { gate })
    }

    fn judge_json(
        &self,
        proposal_json: &str,
        world_json: &str,
        now_ms: f64,
    ) -> Result<String, String> {
        let proposal: ActionProposal =
            serde_json::from_str(proposal_json).map_err(|e| format!("proposal: {e}"))?;
        let world: WorldSnapshot =
            serde_json::from_str(world_json).map_err(|e| format!("world: {e}"))?;
        let decision = self.gate.judge_at(&proposal, &world, to_ms(now_ms)?);
        serde_json::to_string(&decision).map_err(|e| e.to_string())
    }

    fn raise(&mut self, mode: &str) -> Result<bool, String> {
        let mode: Mode = serde_json::from_value(serde_json::Value::String(mode.to_string()))
            .map_err(|_| format!("unknown mode `{mode}`"))?;
        Ok(self.gate.raise_mode(mode))
    }
}

/// Check a policy without building a gate. Returns the error message, or
/// `undefined` when the policy is valid.
#[wasm_bindgen]
pub fn policy_error(policy_json: &str) -> Option<String> {
    Policy::from_json(policy_json).err().map(|e| e.to_string())
}

/// The haetae crate version the simulator was built from.
#[wasm_bindgen]
pub fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

fn mode_name(mode: Mode) -> String {
    serde_json::to_value(mode)
        .ok()
        .and_then(|v| v.as_str().map(str::to_string))
        .unwrap_or_default()
}

/// Convert a JS millisecond timestamp to `u64`, rejecting anything that is
/// not a whole, non-negative, exactly representable number.
fn to_ms(v: f64) -> Result<u64, String> {
    if (0.0..=MAX_SAFE_INTEGER).contains(&v) && v.fract() == 0.0 {
        Ok(v as u64)
    } else {
        Err(format!(
            "now_ms must be a whole number of milliseconds, got {v}"
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const POLICY: &str = r#"{
      "allowed_sources": ["planner"],
      "envelope": { "max_speed": 1.0, "workspace": { "min": {"x":0,"y":0}, "max": {"x":10,"y":10} } }
    }"#;
    const WORLD: &str = r#"{"stamp_ms":1000,"robot":{"pose":{"x":1,"y":1}},"confidence":0.9}"#;

    fn proposal(speed: f64) -> String {
        format!(
            r#"{{"id":1,"source":"planner","timestamp_ms":1000,"action":{{"type":"move_to","goal":{{"x":3,"y":1}},"speed":{speed}}}}}"#
        )
    }

    #[test]
    fn judges_through_the_real_gate() {
        let sim = Sim::from_policy(POLICY).unwrap();
        let d: serde_json::Value =
            serde_json::from_str(&sim.judge_json(&proposal(5.0), WORLD, 1000.0).unwrap()).unwrap();
        assert_eq!(d["verdict"], "jeol");
        assert_eq!(d["speed_cap"], 1.0);
    }

    #[test]
    fn mode_round_trips_by_name() {
        let mut sim = Sim::from_policy(POLICY).unwrap();
        assert_eq!(sim.mode(), "normal");
        assert!(sim.raise("hold").unwrap());
        assert!(!sim.raise("caution").unwrap());
        assert_eq!(sim.mode(), "hold");
        assert!(sim.raise("sideways").is_err());
        sim.reset_mode();
        assert_eq!(sim.mode(), "normal");
    }

    #[test]
    fn bad_inputs_are_errors_not_panics() {
        let sim = Sim::from_policy(POLICY).unwrap();
        assert!(sim
            .judge_json("{}", WORLD, 1000.0)
            .unwrap_err()
            .starts_with("proposal:"));
        assert!(sim
            .judge_json(&proposal(0.5), "[]", 1000.0)
            .unwrap_err()
            .starts_with("world:"));
        for now in [f64::NAN, -1.0, 1.5, 1e300] {
            assert!(sim.judge_json(&proposal(0.5), WORLD, now).is_err(), "{now}");
        }
    }

    #[test]
    fn policy_errors_are_reported() {
        assert_eq!(policy_error(POLICY), None);
        assert!(policy_error(r#"{"envelope":{}}"#).is_some());
        assert!(Sim::from_policy("not json").is_err());
    }
}
