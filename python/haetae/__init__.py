"""Haetae (해태): robot safety & security stack for physical AI (pre-alpha).

Model output is untrusted. Every proposed action is judged by the gate
and receives exactly one Verdict.
"""
from enum import IntEnum

__version__ = "0.0.1"


class Verdict(IntEnum):
    """Result of judging an action proposal. Higher is stricter."""

    YUN = 0   # 통과: allow as proposed
    JEOL = 1  # 감속: allow with a tighter speed cap
    BUL = 2   # 차단: deny

    def stricter(self, other: "Verdict") -> "Verdict":
        """Combine two verdicts; the stricter one wins (tighten-only)."""
        return max(self, other)


__all__ = ["Verdict", "__version__"]
