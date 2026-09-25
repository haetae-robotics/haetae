//! What handling one inbound message produced.

use haetae_core::{Decision, Mode};
use serde::Serialize;

/// The result of [`crate::Runtime::handle`] / [`crate::Runtime::handle_bytes`].
///
/// Serialises as an externally-tagged JSON object —
/// `{"decision":{...}}`, `{"world_updated":{"stamp_ms":...}}`,
/// `{"mode_changed":{"before":"normal","after":"hold"}}`,
/// `{"rejected":{"error":"..."}}` — the shape the W3 ROS 2 node publishes on
/// `~/outcome` (and `~/decision` for the first variant).
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    /// The proposal was judged; carries the gate's answer.
    Decision(Decision),
    /// A world snapshot replaced the previous trusted world.
    WorldUpdated { stamp_ms: u64 },
    /// A fault was applied. `after` equals `before` when the requested mode
    /// was not higher on the ladder.
    ModeChanged { before: Mode, after: Mode },
    /// The bytes were not a well-formed inbound message. Never fatal, never
    /// an incident — garbage must not be able to force log creation.
    Rejected { error: String },
}
