//! Haetae gate: judges untrusted robot action proposals.
//!
//! Model output (VLA, planner, teleop, peers) is treated as untrusted input.
//! Each [`ActionProposal`] is checked against a [`Policy`] using facts from the
//! trusted [`WorldSnapshot`], and receives exactly one [`Verdict`]:
//! `Yun` (allow), `Jeol` (allow with tightened limits) or `Bul` (deny).

mod gate;
mod geom;
mod mode;
mod policy;
mod proposal;
mod verdict;
mod world;

pub use gate::{Decision, Gate};
pub use geom::{Point2, Rect};
pub use mode::Mode;
pub use policy::{
    Condition, Effect, Envelope, Freshness, HumanWithin, Policy, PolicyError, Rule, Zone,
    MAX_FRESHNESS_BUDGET_MS,
};
pub use proposal::{ActionKind, ActionProposal, Source};
pub use verdict::Verdict;
pub use world::{Human, HumanClass, RobotState, WorldSnapshot};
