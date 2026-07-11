"""Pure-Python port of `tools/mining_sim.html` for fast planner evaluation.

Mirrors the HTML simulator's game logic (tape generation, 1x1/2x2/3x3 cluster
placement with isolation, reward only on cluster completion, viewport scroll
when row 6 has any reachable air) so we can run `plan_v4` against it without a
browser. Use this to measure the planner's utility — depth reached, score,
cost-per-pit, item utilisation — over many trials.

Reuses `miner.v3.actions` for dig/bomb/drill mechanics and
`miner.v3.board.promote_after_dig` for reachability propagation, so the sim
sees the same physics the planner reasons about.

Usage:
    python tools/mining_sim_eval.py --runs 20
    python tools/mining_sim_eval.py --runs 100 --seed 42
    python tools/mining_sim_eval.py --runs 1 --log-every 10  # trace one game

Bomb off-screen physics (2026-07-12):
    The game's bomb (3x3 + cross) can affect KNOWN cells below the 7-row
    viewport; drill/pickaxe are viewport-bounded. `_do_bomb` therefore applies
    its footprint in WORLD coordinates (viewport row r -> world row viewport+r),
    opening/collecting below-viewport tape cells, so it matches the planner's
    `miner/final_v1/planner._affected()`. Drill stays screen-only. Before this,
    the sim clamped bombs to 7 rows while the 21-row planner counted the deeper
    hits -> every 21-row evaluation was corrupted by prediction!=execution.

Board-label semantics for a DUG (collected) pit (investigated 2026-07-12,
recorded for later cluster-identity work; planner NOT changed here):
    - sim: `open_cell` writes a collected pit as label "dug_pit" (distinct from
      generic air "empty"; both are air/`is_air`, neither is `is_pit`). Undug
      pits sit in the tape as "unreachable_pit" (promoted to "reachable_pit"
      once a neighbour opens). So `get_board()`/`get_known_board()` CAN tell a
      dug pit ("dug_pit") apart from air that was never a pit ("empty").
    - CNN/ADB runtime: `miner.core.config.DEFAULT_CLASSES` has BOTH "dug_pit"
      and "empty" as separate classes, so the classifier CAN in principle label
      a collected pit as "dug_pit" (imperfect in practice per
      miner/check_dataset_usage.md, but the label exists).
    - WS runtime (`ws_token/mining_adapter._block_label`): a collected pit has
      block count==0 and is projected as plain "empty" -- NEVER "dug_pit". So
      the WS 21-row reconstruction CANNOT distinguish a dug pit from generic
      air; only undug pits (count>0) carry a pit label
      (reachable_pit / offscreen unreachable_pit). Consequence for any future
      cluster-identity preservation: sim + CNN can remember "this air used to be
      a cluster pit"; WS cannot (it loses that identity to "empty").
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from miner.core.mechanics import get_bomb_affected_cells
from miner.v3.actions import (
    dig_cost,
    get_drill_targets,
)
from miner.v3.board import (
    is_air,
    is_frontier_diggable,
    is_pit,
    is_reachable_air,
    is_unreachable,
    normalize_label,
    open_cell,
    promote_after_dig,
)
from miner.planning.smart_planner import plan_smart
from miner.final_v1 import plan_final_v1
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4


def _call_smart(board, shovels, items):
    # plan_smart returns 'steps' as a list of action dicts; same shape as v3/v4.
    return plan_smart(board, shovels=shovels, items=items)


# v2 removed 2026-06-05 (violated the <300ms budget on 18.8% of real boards).
PLANNERS = {
    "v1": _call_smart,
    "v3": plan_v3,
    "v4": plan_v4,
    "final_v1": plan_final_v1,
}

ROWS = 7
COLS = 6
TAPE_INITIAL = 60
TAPE_EXTEND = 30

# --- Empirical mineral model (calibrated via tools/track_pits_replay.py) ---
# CRITICAL: clusters must be measured by TRACKING pits across the scroll/dig
# timeline, NOT by counting connected components in isolated snapshots. A 3x3
# cluster spans 3 tape rows and is collected incrementally as the viewport
# descends, so it never appears as 9 intact pit cells in any single frame --
# per-frame counting wrongly concludes "no 3x3 exist". Time-tracked
# reconstruction over the real logs (59 sessions) shows:
#   - SPAWN pit density = 3.64% of tape cells (single-snapshot standing density
#     is only ~0.99% because pits are collected quickly -- that is NOT the spawn
#     rate). PIT_DENSITY targets the spawn rate; the sim's standing density then
#     lands near the real ~1% (validated by --report in compare_planners).
#   - Clusters are SQUARES 1x1/2x2/3x3 (the original sim's shape design was
#     right). Mix by cluster count: 1x1 66%, 2x2 18%, 3x3 17%. 3x3 are only 17%
#     of clusters but ~52% of pit CELLS, so cluster-aware planning genuinely
#     matters. The original generator's only error was density (~33% spawn, ~9x
#     too high) -- not shape.
PIT_DENSITY = 0.036
# Cluster side-length PMF (square side: 1=1x1, 2=2x2, 3=3x3), by cluster count.
CLUSTER_SIDE_PMF = {1: 0.66, 2: 0.17, 3: 0.17}


@dataclass
class Cluster:
    cells: Set[Tuple[int, int]]  # (tape_row, col)
    total: int


@dataclass
class SimStats:
    score: int = 0
    pits: int = 0           # pit cells from completed clusters only
    depth: int = 0          # rows scrolled past
    digs: int = 0           # number of actions (any tool)
    cost: float = 0.0       # shovel cost (pickaxe digs)
    bombs_used: int = 0
    drills_used: int = 0
    bombs_earned: int = 0
    drills_earned: int = 0
    # Keyed by completed vein size (1..5), not square area -- veins are irregular.
    clusters_completed: Dict[int, int] = field(default_factory=dict)
    lost_pits: int = 0      # uncollected pit cells scrolled out with their cluster


class MiningSim:
    def __init__(
        self,
        rng: Optional[random.Random] = None,
        pickaxes: int = 1000,
        bombs: int = 10,
        drills: int = 10,
    ):
        self.rng = rng or random.Random()
        self.inv = {"pickaxe": pickaxes, "bomb": bombs, "drill": drills}
        self.stats = SimStats()
        self.tape: List[List[str]] = []
        self.viewport: int = 0
        self.clusters: List[Cluster] = []
        self.cell_to_cluster: Dict[Tuple[int, int], Cluster] = {}

        self._extend_tape(TAPE_INITIAL)
        # Foothold: 2 reachable air cells on tape row 0 (no clusters there).
        cols = list(range(COLS))
        self.rng.shuffle(cols)
        for c in cols[:2]:
            self.tape[0][c] = "empty"
        self._recompute_reachability()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def get_board(self) -> List[List[str]]:
        """Return a deep copy of the visible 7×6 window."""
        return [list(self.tape[self.viewport + i]) for i in range(ROWS)]

    def get_known_board(self, rows: int = 7) -> List[List[str]]:
        """Read-only copy of the visible window extended with known tape rows
        below (models the WS 21-row static-terrain reconstruction)."""
        limit = max(ROWS, min(21, int(rows)))
        end = min(len(self.tape), self.viewport + limit)
        return [list(self.tape[index]) for index in range(self.viewport, end)]

    def is_over(self) -> bool:
        return all(self.inv[k] <= 0 for k in self.inv)

    def fallback_step(self) -> Optional[Dict[str, Any]]:
        """Cheapest-progress fallback when a planner returns no steps.

        Digs the lowest-cost reachable frontier cell, preferring pits (collect
        minerals) then deeper rows (drives toward a scroll). This mirrors the
        no_pit behaviour any robust production planner must always provide --
        live, an empty plan is re-planned rather than being fatal, so the sim
        must not treat a single empty plan as game-over. Returns None only when
        the board is genuinely stuck (no diggable frontier cell at all).
        """
        view = self._board_view()
        best: Optional[Tuple[int, int]] = None
        best_key: Optional[Tuple[int, float, int]] = None
        for r in range(ROWS):
            for c in range(COLS):
                if is_frontier_diggable(view, r, c):
                    cell = view[r][c]
                    pit_bonus = 0 if is_pit(cell) else 1  # pits first
                    key = (pit_bonus, dig_cost(cell), -r)  # cheap, then deep
                    if best_key is None or key < best_key:
                        best_key = key
                        best = (r, c)
        if best is None:
            return None
        return {"type": "dig", "pos": best}

    def apply_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a v4 plan step. Returns {'ok', 'scrolled'}."""
        kind = step.get("type")
        pos = step.get("pos")
        if pos is None:
            return {"ok": False, "scrolled": False}
        r, c = int(pos[0]), int(pos[1])

        if kind == "dig":
            return self._do_pickaxe(r, c)
        if kind == "use":
            item = step.get("item")
            if item == "bomb":
                return self._do_bomb(r, c)
            if item == "drill":
                return self._do_drill(r, c)
        return {"ok": False, "scrolled": False}

    # ------------------------------------------------------------------
    # Tape + cluster generation
    # ------------------------------------------------------------------
    def _extend_tape(self, count: int) -> None:
        start_row = len(self.tape)
        for i in range(count):
            depth = start_row + i
            dirt_p = max(0.30, 0.55 - depth * 0.02)
            rock_p = min(0.55, 0.25 + depth * 0.02)
            row = [self._roll_cell(dirt_p, rock_p) for _ in range(COLS)]
            self.tape.append(row)
        self._place_clusters_in_range(start_row, len(self.tape))

    def _roll_cell(self, dirt_p: float, rock_p: float) -> str:
        empty_p = 0.08
        x = self.rng.random()
        if x < empty_p:
            return "unreachable_empty"
        acc = empty_p
        if x < acc + dirt_p:
            return "unreachable_dirt"
        acc += dirt_p
        if x < acc + rock_p:
            return "unreachable_rock"
        return "unreachable_dirt"

    def _place_clusters_in_range(self, r_min: int, r_max: int) -> None:
        """Seed SQUARE mineral clusters (1x1/2x2/3x3) at the empirical density
        and size mix (time-tracked from real logs via track_pits_replay.py).

        Draws each cluster's side from ``CLUSTER_SIDE_PMF`` and places it as an
        isolated square (1-cell ring clear of other pits) until ~``PIT_DENSITY``
        of the row range is pit. 3x3 are only 17% of clusters but ~52% of pit
        cells, so this regime is genuinely cluster-rich -- unlike the per-frame
        illusion that "no 3x3 exist".
        """
        from_row = max(r_min, 1)  # tape row 0 reserved for player foothold
        span = max(0, r_max - from_row)
        if span <= 0:
            return
        target_pits = round(PIT_DENSITY * span * COLS)
        placed = 0
        guard = 0
        guard_max = target_pits * 25 + 50
        while placed < target_pits and guard < guard_max:
            guard += 1
            side = self._draw_cluster_side()
            cells = self._try_place_square(side, from_row, r_max)
            if cells is not None:
                placed += len(cells)

    def _draw_cluster_side(self) -> int:
        x = self.rng.random()
        acc = 0.0
        for side, p in CLUSTER_SIDE_PMF.items():
            acc += p
            if x < acc:
                return side
        return 1

    def _try_place_square(
        self, side: int, r_min: int, r_max: int, attempts: int = 60
    ) -> Optional[Set[Tuple[int, int]]]:
        hi = max(r_min, r_max - side)
        if hi < r_min or side > COLS:
            return None
        for _ in range(attempts):
            r = self.rng.randint(r_min, hi)
            c = self.rng.randint(0, COLS - side)
            if self._can_place_square(r, c, side):
                return self._paint_square(r, c, side)
        return None

    def _can_place_square(self, r: int, c: int, side: int) -> bool:
        # Footprint must fit and be free of air / existing pits, and stay off
        # tape row 0 (reserved for the player's foothold).
        if r < 1:
            return False
        for dr in range(side):
            for dc in range(side):
                nr, nc = r + dr, c + dc
                if nr >= len(self.tape) or nc < 0 or nc >= COLS:
                    return False
                cell = self.tape[nr][nc]
                if is_air(cell) or "pit" in cell:
                    return False
        # 1-cell isolation ring: no other pit may touch the footprint.
        for dr in range(-1, side + 1):
            for dc in range(-1, side + 1):
                if 0 <= dr < side and 0 <= dc < side:
                    continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < len(self.tape) and 0 <= nc < COLS:
                    if "pit" in self.tape[nr][nc]:
                        return False
        return True

    def _paint_square(self, r: int, c: int, side: int) -> Set[Tuple[int, int]]:
        cells: Set[Tuple[int, int]] = set()
        cluster = Cluster(cells=set(), total=side * side)
        for dr in range(side):
            for dc in range(side):
                nr, nc = r + dr, c + dc
                self.tape[nr][nc] = "unreachable_pit"
                cluster.cells.add((nr, nc))
                self.cell_to_cluster[(nr, nc)] = cluster
                cells.add((nr, nc))
        self.clusters.append(cluster)
        return cells

    # ------------------------------------------------------------------
    # Action helpers
    # ------------------------------------------------------------------
    def _board_view(self) -> List[List[str]]:
        # References to tape rows — mutations propagate back.
        return [self.tape[self.viewport + i] for i in range(ROWS)]

    def _do_pickaxe(self, r: int, c: int) -> Dict[str, Any]:
        view = self._board_view()
        if not is_frontier_diggable(view, r, c):
            return {"ok": False, "scrolled": False}
        cell = view[r][c]
        cost = dig_cost(cell)
        if self.inv["pickaxe"] < cost:
            return {"ok": False, "scrolled": False}
        self.inv["pickaxe"] -= cost
        self.stats.cost += cost
        self.stats.digs += 1
        pit_hit = is_pit(cell)
        open_cell(view, r, c)
        promote_after_dig(view, [(r, c)])
        if pit_hit:
            self._on_pit_dug([(self.viewport + r, c)])
        return self._post_action()

    def _do_bomb(self, r: int, c: int) -> Dict[str, Any]:
        view = self._board_view()
        if not is_reachable_air(view[r][c]):
            return {"ok": False, "scrolled": False}
        if self.inv["bomb"] <= 0:
            return {"ok": False, "scrolled": False}
        self.inv["bomb"] -= 1
        self.stats.digs += 1
        self.stats.bombs_used += 1
        # 炸彈以 WORLD 座標套用 footprint：viewport row r → world row viewport+r，
        # 3x3+十字可延伸至 viewport 下方的 tape 列（真實遊戲炸彈作用於已知畫面外格；
        # 這與 planner _affected() 對 bomb 計入畫面外命中一致）。rows 上限取 tape
        # 尾端 = viewport 相對可及列數，footprint 只到 r+2，正常盤面必存在。
        max_rel = len(self.tape) - self.viewport
        affected = get_bomb_affected_cells(r, c, max_rel, COLS)
        max_tr = max((tr for tr, _ in affected), default=r)
        win_rows = max(ROWS, max_tr + 1)
        # 視窗含 viewport 內外列，皆為 tape row 參照 → 開挖/promote 直接寫回 world
        work = [self.tape[self.viewport + i] for i in range(win_rows)]
        pit_hits = [
            (self.viewport + tr, tc)
            for (tr, tc) in affected
            if is_pit(work[tr][tc])
        ]
        for (tr, tc) in affected:
            open_cell(work, tr, tc)
        promote_after_dig(work, affected)
        if pit_hits:
            self._on_pit_dug(pit_hits)
        return self._post_action()

    def _do_drill(self, r: int, c: int) -> Dict[str, Any]:
        view = self._board_view()
        if not is_reachable_air(view[r][c]):
            return {"ok": False, "scrolled": False}
        if self.inv["drill"] <= 0:
            return {"ok": False, "scrolled": False}
        self.inv["drill"] -= 1
        self.stats.digs += 1
        self.stats.drills_used += 1
        affected = get_drill_targets(r, c, ROWS, COLS)
        pit_hits = [
            (self.viewport + tr, tc)
            for (tr, tc) in affected
            if is_pit(view[tr][tc])
        ]
        for (tr, tc) in affected:
            open_cell(view, tr, tc)
        promote_after_dig(view, affected)
        if pit_hits:
            self._on_pit_dug(pit_hits)
        return self._post_action()

    # ------------------------------------------------------------------
    # Reward, scroll, reachability
    # ------------------------------------------------------------------
    def _on_pit_dug(self, tape_positions: List[Tuple[int, int]]) -> None:
        completed: List[Cluster] = []
        for pos in tape_positions:
            cluster = self.cell_to_cluster.pop(pos, None)
            if cluster is None:
                continue
            cluster.cells.discard(pos)
            if not cluster.cells:
                completed.append(cluster)
        for cluster in completed:
            n = cluster.total
            self.stats.score += n * 10 + n * (n - 1) * 2
            self.stats.pits += n
            self.stats.clusters_completed[n] = self.stats.clusters_completed.get(n, 0) + 1
            item_count = max(1, min(3, (n + 1) // 2))
            item_type = "bomb" if self.rng.random() < 0.5 else "drill"
            self.inv[item_type] += item_count
            if item_type == "bomb":
                self.stats.bombs_earned += item_count
            else:
                self.stats.drills_earned += item_count
            try:
                self.clusters.remove(cluster)
            except ValueError:
                pass

    def _post_action(self) -> Dict[str, Any]:
        scrolled = False
        # Same recompute pattern as JS: refresh reachability, then auto-scroll
        # while row 6 has any reachable air cell.
        self._recompute_reachability()
        safety = 0
        while self._floor7_open() and safety < 10:
            self._scroll_down()
            scrolled = True
            safety += 1
        return {"ok": True, "scrolled": scrolled}

    def _floor7_open(self) -> bool:
        last = self.tape[self.viewport + ROWS - 1]
        return any(is_reachable_air(cell) for cell in last)

    def _scroll_down(self) -> None:
        self.viewport += 1
        if self.viewport + ROWS > len(self.tape):
            self._extend_tape(TAPE_EXTEND)
        self._prune_offscreen_clusters()
        self.stats.depth += 1
        self._recompute_reachability()

    def _prune_offscreen_clusters(self) -> None:
        survivors: List[Cluster] = []
        for cluster in self.clusters:
            lost = any(r < self.viewport for (r, _) in cluster.cells)
            if lost:
                self.stats.lost_pits += len(cluster.cells)
                for pos in cluster.cells:
                    self.cell_to_cluster.pop(pos, None)
            else:
                survivors.append(cluster)
        self.clusters = survivors

    def _recompute_reachability(self) -> None:
        view = self._board_view()
        seeds = [
            (r, c)
            for r in range(ROWS)
            for c in range(COLS)
            if is_reachable_air(view[r][c])
        ]
        if seeds:
            promote_after_dig(view, seeds)


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------
def play_one_game(
    seed: Optional[int] = None,
    max_iter: int = 2000,
    log_every: int = 0,
    starting_inv: Optional[Dict[str, int]] = None,
    planner: str = "v4",
    action_budget: Optional[int] = None,
    known_rows: int = 7,
    exec_mode: str = "plan",
) -> Dict[str, Any]:
    """Play one game with the given planner.

    ``action_budget`` (optional) caps the number of *executed actions* (digs +
    item uses), modelling the real ~6-minute session wall clock where each
    action costs ~7-8 s (screenshot + classify + execute). ``None`` = uncapped
    (original behaviour, terminates on shovel/item exhaustion or ``max_iter``).

    ``exec_mode`` aligns the sim with the two real runtimes:
      "plan" — 每次規劃執行整份 plan（CNN/ADB `execute_plan_steps` 語意）。
      "step" — 每次規劃只執行第一步再重規劃（WS `mine_until_pickaxe_empty`
               supervised loop 語意；生產 WS 的 v1 也是一次一步）。
    兩種真實 runtime 都是「鎬子用完就停」（CNN `while count >= 1` / WS
    `pickaxe_empty`），所以本函式在鎬子歸零時終止，不再等所有道具耗盡。
    """
    plan_fn = PLANNERS[planner]
    rng = random.Random(seed) if seed is not None else random.Random()
    inv = starting_inv or {"pickaxe": 1000, "bomb": 10, "drill": 10}
    sim = MiningSim(
        rng=rng,
        pickaxes=inv["pickaxe"],
        bombs=inv["bomb"],
        drills=inv["drill"],
    )

    iter_count = 0
    plan_calls = 0
    plan_total_ms = 0.0
    plan_times_ms: List[float] = []
    empty_plan_count = 0
    fallback_count = 0
    rejected_count = 0
    actions_taken = 0
    pit_density_samples: List[float] = []

    while iter_count < max_iter:
        if sim.is_over():
            break
        # 真實 runtime 是鎬子綁定：CNN 迴圈 `while count >= 1`、WS 挖到
        # pickaxe_empty 即停（道具剩著也不會繼續）。
        if sim.inv["pickaxe"] <= 0:
            break
        if action_budget is not None and actions_taken >= action_budget:
            break
        board = sim.get_board()
        # Standing pit density in the visible viewport (calibration check).
        pit_in_view = sum(1 for row in board for cell in row if is_pit(cell))
        pit_density_samples.append(pit_in_view / (ROWS * COLS))
        plan_calls += 1
        t0 = time.perf_counter()
        if planner == "final_v1":
            # 21 列已知盤只給 final_v1（比較「WS 已知視野整合」的增益）；
            # 其他 planner 維持 7 列可見盤 = 生產基準。
            plan_board = sim.get_known_board(known_rows)
            plan = plan_fn(
                plan_board,
                shovels=float(sim.inv["pickaxe"]),
                items={"drill": sim.inv["drill"], "bomb": sim.inv["bomb"]},
                visible_rows=ROWS,
                # step 模式讓 planner 用 RHO_ACTION_STEP 對齊 KPI（WS 每步重規劃
                # 只取一步）；plan 模式維持整批執行的成本語意。exec_mode 已是
                # "plan"|"step"，直接映射到 planner 的 exec_profile。
                exec_profile=exec_mode,
            )
        else:
            plan = plan_fn(
                board,
                shovels=float(sim.inv["pickaxe"]),
                items={"drill": sim.inv["drill"], "bomb": sim.inv["bomb"]},
            )
        plan_ms = (time.perf_counter() - t0) * 1000.0
        plan_total_ms += plan_ms
        plan_times_ms.append(plan_ms)

        steps = plan.get("steps") or []
        if exec_mode == "step":
            steps = steps[:1]
        if not steps:
            # Production-realistic fallback: an empty plan is not fatal live
            # (the bot re-plans). Dig the cheapest reachable frontier cell so
            # the session continues; only a board with no diggable frontier at
            # all is genuinely stuck.
            fb = sim.fallback_step()
            if fb is None:
                empty_plan_count += 1
                break
            res = sim.apply_step(fb)
            if not res["ok"]:
                empty_plan_count += 1
                break
            fallback_count += 1
            actions_taken += 1
            iter_count += 1
            continue

        for step in steps:
            res = sim.apply_step(step)
            if not res["ok"]:
                rejected_count += 1
                break
            actions_taken += 1
            if action_budget is not None and actions_taken >= action_budget:
                break
            if res["scrolled"]:
                break

        iter_count += 1
        if log_every and iter_count % log_every == 0:
            s = sim.stats
            print(
                f"  [iter {iter_count}] depth={s.depth} score={s.score} pits={s.pits} "
                f"cost={s.cost:.0f} bombs={sim.inv['bomb']} drills={sim.inv['drill']} "
                f"pick={sim.inv['pickaxe']}"
            )

    # Half-clear waste: pit cells dug in clusters still on-screen but never
    # completed when the session ended (those shovels/items earned NO ore,
    # because ore is awarded only on FULL cluster clear).
    wasted_partial = sum(
        c.total - len(c.cells) for c in sim.clusters if 0 < len(c.cells) < c.total
    )

    return {
        "stats": sim.stats,
        "inv": dict(sim.inv),
        "init_inv": inv,
        "iters": iter_count,
        "actions": actions_taken,
        "plan_calls": plan_calls,
        "plan_avg_ms": (plan_total_ms / plan_calls) if plan_calls else 0.0,
        "plan_times_ms": plan_times_ms,
        "empty_plan": empty_plan_count > 0,
        "fallbacks": fallback_count,
        "rejected": rejected_count,
        "lost_pits": sim.stats.lost_pits,
        "unfinished_clusters": sum(
            1 for c in sim.clusters if 0 < len(c.cells) < c.total
        ),
        "clusters": dict(sim.stats.clusters_completed),
        "wasted_partial": wasted_partial,
        "standing_pit_density": (
            statistics.mean(pit_density_samples) if pit_density_samples else 0.0
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--max-iter", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log-every", type=int, default=0)
    parser.add_argument(
        "--planner",
        choices=["v1", "v3", "v4", "final_v1"],
        default="v1",
        help="which planner to evaluate (v1=plan_smart)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="skip per-run output, show aggregate only"
    )
    args = parser.parse_args()

    print(f"=== Planner: {args.planner} ===")
    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    for i in range(args.runs):
        seed = (args.seed + i) if args.seed is not None else None
        result = play_one_game(
            seed=seed,
            max_iter=args.max_iter,
            log_every=args.log_every,
            planner=args.planner,
        )
        results.append(result)
        if not args.quiet:
            s = result["stats"]
            print(
                f"Run {i+1:3d}: depth={s.depth:3d} score={s.score:5d} pits={s.pits:3d} "
                f"cost={s.cost:5.0f} bombs={s.bombs_used}/{s.bombs_earned + result['init_inv']['bomb']} "
                f"drills={s.drills_used}/{s.drills_earned + result['init_inv']['drill']} "
                f"clusters={s.clusters_completed} iters={result['iters']} "
                f"plan_avg={result['plan_avg_ms']:.1f}ms"
                f"{' (stuck)' if result['empty_plan'] else ''}"
            )
    elapsed = time.perf_counter() - t0
    print(
        f"\n{args.runs} games in {elapsed:.2f}s "
        f"({elapsed / max(1, args.runs) * 1000:.0f}ms/game)"
    )

    print("\n=== Aggregate ===")
    metrics = ("score", "pits", "depth", "cost", "digs")
    for k in metrics:
        vals = [getattr(r["stats"], k) for r in results]
        avg = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        print(f"  {k:7s} avg={avg:8.1f}  stdev={sd:6.1f}  min={min(vals):.0f}  max={max(vals):.0f}")

    pits_per_shovel = [
        (r["stats"].pits / r["stats"].cost) if r["stats"].cost else 0.0
        for r in results
    ]
    score_per_shovel = [
        (r["stats"].score / r["stats"].cost) if r["stats"].cost else 0.0
        for r in results
    ]
    print(
        f"\n  pits/shovel  avg={statistics.mean(pits_per_shovel):.2f}  "
        f"score/shovel avg={statistics.mean(score_per_shovel):.2f}"
    )

    # Item utilisation: bombs/drills used / bombs/drills available (init + earned)
    bombs_avail = sum(r["stats"].bombs_earned + r["init_inv"]["bomb"] for r in results)
    bombs_used = sum(r["stats"].bombs_used for r in results)
    drills_avail = sum(r["stats"].drills_earned + r["init_inv"]["drill"] for r in results)
    drills_used = sum(r["stats"].drills_used for r in results)
    print(
        f"  bombs used:  {bombs_used}/{bombs_avail} "
        f"({100 * bombs_used / bombs_avail if bombs_avail else 0:.1f}%)"
    )
    print(
        f"  drills used: {drills_used}/{drills_avail} "
        f"({100 * drills_used / drills_avail if drills_avail else 0:.1f}%)"
    )

    # Vein completion breakdown (by vein size 1..5)
    vein_counts: Dict[int, int] = {}
    for r in results:
        for k, v in r["stats"].clusters_completed.items():
            vein_counts[k] = vein_counts.get(k, 0) + v
    total = sum(vein_counts.values())
    parts = "  ".join(f"size{k}={vein_counts[k]}" for k in sorted(vein_counts))
    print(f"  veins:       {parts}  total={total}")

    # Standing pit density (calibration vs real-game 0.99%)
    dens = [r["standing_pit_density"] for r in results]
    print(
        f"  standing pit density: avg={100*statistics.mean(dens):.3f}%  "
        f"(real-game target ~0.99%)"
    )

    stuck_count = sum(1 for r in results if r["empty_plan"])
    if stuck_count:
        print(f"  WARNING: {stuck_count}/{args.runs} games ended on planner returning 0 steps")


if __name__ == "__main__":
    main()
