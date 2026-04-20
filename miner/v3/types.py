from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

Board = List[List[str]]
Coordinate = Tuple[int, int]


@dataclass
class PlanStats:
    """Per-plan accounting so the operator can see resource efficiency."""
    explored_nodes: int = 0
    elapsed_ms: float = 0.0
    pits_at_start: int = 0
    pits_collected: int = 0
    pits_remaining: int = 0
    pits_unreachable_remaining: int = 0
    shovel_cost: float = 0.0
    drills_used: int = 0
    bombs_used: int = 0
    cost_per_pit: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "explored_nodes": self.explored_nodes,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "pits_at_start": self.pits_at_start,
            "pits_collected": self.pits_collected,
            "pits_remaining": self.pits_remaining,
            "pits_unreachable_remaining": self.pits_unreachable_remaining,
            "shovel_cost": round(self.shovel_cost, 2),
            "drills_used": self.drills_used,
            "bombs_used": self.bombs_used,
            "cost_per_pit": round(self.cost_per_pit, 3),
        }


@dataclass
class PlanResult:
    ok: bool
    message: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    stats: PlanStats = field(default_factory=PlanStats)
    strategy_class: str = ""
    floor7_open: bool = False
    exit_guard_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "steps": self.steps,
            "strategy_class": self.strategy_class,
            "floor7_open": self.floor7_open,
            "exit_guard_required": self.exit_guard_required,
            "stats": self.stats.to_dict(),
            # Mining service still keys off these legacy fields.
            "remaining_pits": self.stats.pits_remaining,
            "total_cost": self.stats.shovel_cost,
            "explored_nodes": self.stats.explored_nodes,
            "elapsed_ms": self.stats.elapsed_ms,
        }
