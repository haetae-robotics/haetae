//! Haetae (해태): robot safety & security stack for physical AI (pre-alpha).
//!
//! Model output is untrusted. Every proposed action is judged by the gate
//! and receives exactly one [`Verdict`].

/// Result of judging an action proposal.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Verdict {
    /// 允 — allow as proposed.
    Yun,
    /// 節 — allow with tightened limits (speed, force, workspace).
    Jeol,
    /// 不 — deny.
    Bul,
}

impl Verdict {
    /// Combine two verdicts; the stricter one wins (tighten-only).
    pub fn stricter(self, other: Verdict) -> Verdict {
        use Verdict::*;
        match (self, other) {
            (Bul, _) | (_, Bul) => Bul,
            (Jeol, _) | (_, Jeol) => Jeol,
            _ => Yun,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Verdict::*;

    #[test]
    fn stricter_wins() {
        assert_eq!(Yun.stricter(Jeol), Jeol);
        assert_eq!(Jeol.stricter(Bul), Bul);
        assert_eq!(Yun.stricter(Yun), Yun);
    }
}
