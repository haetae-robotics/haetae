//! Transport-level message types and parsing.

use haetae_core::{ActionProposal, Mode, WorldSnapshot};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// One inbound message from the transport.
///
/// There is deliberately **no** reset variant: nothing on any transport may
/// move the mode down the ladder. An operator reset will arrive as a signed
/// command (W3+), never as an `Inbound`.
#[derive(Debug, Clone, PartialEq)]
pub enum Inbound {
    /// New facts from the trusted safety-perception path.
    World(WorldSnapshot),
    /// A fault reported by maek (self-diagnosis). Can only raise the mode.
    Fault(Fault),
    /// An action proposal from the untrusted side.
    Proposal(ActionProposal),
}

impl Inbound {
    /// Parse one transport message (one JSON object).
    ///
    /// Dispatch is on the top-level key, explicitly: a `world` or `fault`
    /// key must be the *only* key, otherwise the message is parsed as an
    /// [`ActionProposal`]. An untagged serde enum would pick the first
    /// variant that fits and silently drop extra keys — a message mixing
    /// `fault` and `world` could lose the fault — so ambiguous input is an
    /// error here, never a partial parse.
    pub fn from_json(bytes: &[u8]) -> Result<Inbound, String> {
        let value: Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
        let obj = value.as_object().ok_or("expected a JSON object")?;
        let event = |key: &str| -> Result<Option<Value>, String> {
            match (obj.get(key), obj.len()) {
                (None, _) => Ok(None),
                (Some(v), 1) => Ok(Some(v.clone())),
                (Some(_), _) => Err(format!("`{key}` must be the only key in the message")),
            }
        };
        let err = |e: serde_json::Error| e.to_string();
        if let Some(w) = event("world")? {
            return serde_json::from_value(w).map(Inbound::World).map_err(err);
        }
        if let Some(f) = event("fault")? {
            return serde_json::from_value(f).map(Inbound::Fault).map_err(err);
        }
        serde_json::from_value(value)
            .map(Inbound::Proposal)
            .map_err(err)
    }
}

/// A fault reported by maek (self-diagnosis).
///
/// `timestamp_ms` is the reporter's claim; it is kept inside log payloads
/// only. The log entry's `ts_ms` is always the trusted receive time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Fault {
    pub code: String,
    pub timestamp_ms: u64,
    /// Mode to raise to. The ladder only goes up, so naming a mode below the
    /// current one is recorded but changes nothing.
    pub raise_to: Mode,
}
