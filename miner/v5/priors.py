"""Probabilistic priors for the v5 mining planner.

Loads `miner/v5/priors.json` (auto-generated from 343 real sessions by
`tools/build_v5_priors.py`) once at module import and caches it. If the file is
missing or malformed, falls back to hard-coded constants that mirror the same
numbers (logged as a warning) so the planner never hard-fails on a fresh
checkout.

Pooled priors only — no depth bucketing (the depth drift is statistically
significant but small in effect, and sessions rarely descend past ~100 rows;
see docs/MINING_V5_PRIORS.md §5).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

_PRIORS_PATH = Path(__file__).resolve().parent / "priors.json"

# ---------------------------------------------------------------------------
# Hard-coded fallback — same numbers as the shipped priors.json (2026-06-12,
# 343 sessions). Used only when the JSON can't be read.
# ---------------------------------------------------------------------------
_FALLBACK_MARGINAL = {"air": 0.186, "dirt": 0.554, "rock": 0.229, "pit": 0.031}
_FALLBACK_V_BELOW_GIVEN_ABOVE = {
    "air": {"air": 0.279, "dirt": 0.484, "rock": 0.220, "pit": 0.017},
    "dirt": {"air": 0.170, "dirt": 0.570, "rock": 0.242, "pit": 0.018},
    "rock": {"air": 0.131, "dirt": 0.619, "rock": 0.226, "pit": 0.024},
    "pit": {"air": 0.132, "dirt": 0.310, "rock": 0.133, "pit": 0.425},
}
# P(pit directly below | pit run of width w sits at the bottom edge).
_FALLBACK_PIT_BELOW_BY_WIDTH = {1: 0.021, 2: 0.430, 3: 0.767}


@dataclass(frozen=True)
class Priors:
    """Immutable view of the priors the planner consumes."""

    marginal: Dict[str, float]
    below_given_above: Dict[str, Dict[str, float]]
    pit_below_by_width: Dict[int, float]
    source: str  # "priors.json" or "fallback"
    # Per-`above` sample size for the vertical transition table. Lets the
    # runtime-merge layer (miner/v5/priors_runtime.py) cap online counts as a
    # fraction of the offline base without re-reading priors.json. Empty when
    # unknown (fallback path).
    below_given_above_n: Dict[str, int] = field(default_factory=dict)

    # Lift of P(air below | air above) over marginal air — used to discount the
    # expected future cost of descending through a known air column.
    @property
    def air_below_air(self) -> float:
        return self.below_given_above["air"]["air"]

    @property
    def pit_below_pit(self) -> float:
        return self.below_given_above["pit"]["pit"]

    def expected_dig_cost_below(self, above_label: str) -> float:
        """Expected pickaxe cost of the cell directly below a cell labelled
        `above_label`, given the vertical transition priors. dirt/pit cost 1,
        rock costs 2, air costs 0.
        """
        probs = self.below_given_above.get(above_label, self.below_given_above["dirt"])
        return (
            probs["air"] * 0.0
            + probs["dirt"] * 1.0
            + probs["pit"] * 1.0
            + probs["rock"] * 2.0
        )

    def pit_below_prob(self, width: int) -> float:
        """P(pit directly below | pit run width). Widths >3 clamp to 3."""
        w = max(1, min(3, int(width)))
        return self.pit_below_by_width.get(w, _FALLBACK_PIT_BELOW_BY_WIDTH[w])


def _fallback() -> Priors:
    return Priors(
        marginal=dict(_FALLBACK_MARGINAL),
        below_given_above={k: dict(v) for k, v in _FALLBACK_V_BELOW_GIVEN_ABOVE.items()},
        pit_below_by_width=dict(_FALLBACK_PIT_BELOW_BY_WIDTH),
        source="fallback",
    )


def _load_from_json(path: Path) -> Priors:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    marginal = {
        k: float(v["prob"]) for k, v in raw["overall_label_distribution"].items()
    }
    below = {
        above: {lbl: float(p) for lbl, p in node["probs"].items()}
        for above, node in raw["vertical_conditional_P_below_given_above"].items()
    }
    below_n = {
        above: int(node.get("n", 0))
        for above, node in raw["vertical_conditional_P_below_given_above"].items()
    }
    pit_below = {
        int(w): float(node["p_pit_below"])
        for w, node in raw["cluster_square_prior_P_pit_below_given_pit_width"].items()
    }
    return Priors(
        marginal=marginal,
        below_given_above=below,
        pit_below_by_width=pit_below,
        source="priors.json",
        below_given_above_n=below_n,
    )


def load_priors() -> Priors:
    try:
        return _load_from_json(_PRIORS_PATH)
    except (OSError, ValueError, KeyError) as exc:
        logger.warning(
            "miner.v5: could not load priors.json (%s) — using hard-coded fallback",
            exc,
        )
        return _fallback()


# Module-level cache (priors are static for a process lifetime).
_CACHED: Priors | None = None


def get_priors() -> Priors:
    global _CACHED
    if _CACHED is None:
        _CACHED = load_priors()
    return _CACHED


__all__ = ["Priors", "get_priors", "load_priors"]
