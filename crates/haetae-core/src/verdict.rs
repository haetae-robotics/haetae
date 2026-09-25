use serde::{Deserialize, Serialize};

/// Result of judging an action proposal. Ordered from least to most strict.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
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
        self.max(other)
    }
}

#[cfg(test)]
mod tests {
    use super::Verdict::*;

    #[test]
    fn stricter_wins() {
        assert_eq!(Yun.stricter(Jeol), Jeol);
        assert_eq!(Jeol.stricter(Bul), Bul);
        assert_eq!(Bul.stricter(Yun), Bul);
        assert_eq!(Yun.stricter(Yun), Yun);
    }
}
