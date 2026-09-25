use serde::{Deserialize, Serialize};

/// Safety mode ladder. The gate only moves up this ladder on its own;
/// moving down requires an explicit operator reset.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default, Serialize, Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Normal,
    /// Speed is capped at half the envelope maximum.
    Caution,
    /// Everything except `Stop` is denied.
    Hold,
    SafePark,
    EStop,
}

impl Mode {
    /// Whether the robot may only stop in this mode.
    pub fn stop_only(self) -> bool {
        self >= Mode::Hold
    }
}
