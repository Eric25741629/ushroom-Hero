"""final_v1 planner contract types (immutable)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Tuple

ActionKind = Literal["dig", "use"]
ItemName = Literal["pickaxe", "drill", "bomb"]
ActionKey = Tuple[ActionKind, ItemName, int, int]


@dataclass(frozen=True)
class PlannerAction:
    kind: ActionKind
    item: ItemName
    row: int
    col: int

    @property
    def key(self) -> ActionKey:
        return (self.kind, self.item, self.row, self.col)

    def to_step(self, step_cost: float) -> dict:
        step = {
            "type": self.kind,
            "pos": (self.row, self.col),
            "target": (self.row, self.col),
            "step_cost": step_cost,
        }
        if self.kind == "use":
            step["item"] = self.item
        return step


@dataclass(frozen=True)
class PlannerConfig:
    # 6/32/12 實測優於 10/24/8（深而窄反而 stuck+unfin 上升）；
    # 遠礦視野由位能場（PIT_PULL）補，不靠加深
    max_depth: int = 6
    beam_width: int = 32
    branch_width: int = 12
    time_budget_ms: float = 250.0


@dataclass(frozen=True)
class SearchUsage:
    shovels: float = 0.0
    bombs: int = 0
    drills: int = 0
    # 逐次使用的加權成本累計（依該次命中礦坑格數分級，見 scoring.item_use_cost）；
    # 不能事後用「顆數 x 固定價」回推，因為每次使用的分級可能不同
    item_cost_units: float = 0.0


@dataclass(frozen=True)
class ScoreBreakdown:
    cluster_gain: float = 0.0
    pit_gain: float = 0.0
    shovel_cost: float = 0.0
    item_cost: float = 0.0
    lost_pit_penalty: float = 0.0
    unfinished_cluster_penalty: float = 0.0
    descent_bonus: float = 0.0
    path_bonus: float = 0.0
    pull_bonus: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.cluster_gain + self.pit_gain + self.descent_bonus + self.path_bonus
            + self.pull_bonus
            - self.shovel_cost - self.item_cost
            - self.lost_pit_penalty - self.unfinished_cluster_penalty
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "total": self.total}
