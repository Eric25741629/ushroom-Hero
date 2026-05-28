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

from miner.v3.actions import (
    dig_cost,
    get_bomb_targets,
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
from miner.v2.planner import plan_v2
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4


def _call_smart(board, shovels, items):
    # plan_smart returns 'steps' as a list of action dicts; same shape as v2-v4.
    return plan_smart(board, shovels=shovels, items=items)


PLANNERS = {
    "v1": _call_smart,
    "v2": plan_v2,
    "v3": plan_v3,
    "v4": plan_v4,
}

ROWS = 7
COLS = 6
TAPE_INITIAL = 60
TAPE_EXTEND = 30


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
    clusters_completed: Dict[int, int] = field(
        default_factory=lambda: {1: 0, 4: 0, 9: 0}
    )


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

    def is_over(self) -> bool:
        return all(self.inv[k] <= 0 for k in self.inv)

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
        from_row = max(r_min, 1)  # tape row 0 reserved for player foothold
        span = max(0, r_max - from_row)
        n3 = max(1, span // 8)
        for _ in range(n3):
            self._try_place_cluster(3, (from_row, r_max - 3), (0, COLS - 3), 80)
        n2 = max(2, span // 5)
        for _ in range(n2):
            self._try_place_cluster(2, (from_row, r_max - 2), (0, COLS - 2), 50)
        n1 = max(2, span // 5)
        for _ in range(n1):
            self._try_place_cluster(1, (from_row, r_max - 1), (0, COLS - 1), 50)

    def _try_place_cluster(
        self,
        size: int,
        r_range: Tuple[int, int],
        c_range: Tuple[int, int],
        attempts: int,
    ) -> bool:
        if r_range[1] < r_range[0] or c_range[1] < c_range[0]:
            return False
        for _ in range(attempts):
            r = self.rng.randint(r_range[0], r_range[1])
            c = self.rng.randint(c_range[0], c_range[1])
            if self._can_place_cluster(r, c, size):
                self._paint_cluster(r, c, size)
                return True
        return False

    def _can_place_cluster(self, r: int, c: int, size: int) -> bool:
        # Footprint must fit, be free of air and pits.
        for dr in range(size):
            for dc in range(size):
                nr, nc = r + dr, c + dc
                if nr >= len(self.tape) or nc < 0 or nc >= COLS:
                    return False
                cell = self.tape[nr][nc]
                if is_air(cell) or "pit" in cell:
                    return False
        # 1-cell ring around the footprint must contain no other pits.
        for dr in range(-1, size + 1):
            for dc in range(-1, size + 1):
                if 0 <= dr < size and 0 <= dc < size:
                    continue
                nr, nc = r + dr, c + dc
                if nr < 0 or nr >= len(self.tape) or nc < 0 or nc >= COLS:
                    continue
                if "pit" in self.tape[nr][nc]:
                    return False
        return True

    def _paint_cluster(self, r: int, c: int, size: int) -> None:
        cluster = Cluster(cells=set(), total=size * size)
        for dr in range(size):
            for dc in range(size):
                nr, nc = r + dr, c + dc
                self.tape[nr][nc] = "unreachable_pit"
                cluster.cells.add((nr, nc))
                self.cell_to_cluster[(nr, nc)] = cluster
        self.clusters.append(cluster)

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
        affected, _ = get_bomb_targets(r, c, ROWS, COLS)
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
) -> Dict[str, Any]:
    """Play one game with the given planner.

    ``action_budget`` (optional) caps the number of *executed actions* (digs +
    item uses), modelling the real ~6-minute session wall clock where each
    action costs ~7-8 s (screenshot + classify + execute). ``None`` = uncapped
    (original behaviour, terminates on shovel/item exhaustion or ``max_iter``).
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
    empty_plan_count = 0
    actions_taken = 0

    while iter_count < max_iter:
        if sim.is_over():
            break
        if action_budget is not None and actions_taken >= action_budget:
            break
        board = sim.get_board()
        plan_calls += 1
        t0 = time.perf_counter()
        plan = plan_fn(
            board,
            shovels=float(sim.inv["pickaxe"]),
            items={"drill": sim.inv["drill"], "bomb": sim.inv["bomb"]},
        )
        plan_total_ms += (time.perf_counter() - t0) * 1000.0

        steps = plan.get("steps") or []
        if not steps:
            empty_plan_count += 1
            break

        for step in steps:
            res = sim.apply_step(step)
            if not res["ok"]:
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
        "empty_plan": empty_plan_count > 0,
        "clusters": dict(sim.stats.clusters_completed),
        "wasted_partial": wasted_partial,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--max-iter", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log-every", type=int, default=0)
    parser.add_argument(
        "--planner",
        choices=["v1", "v2", "v3", "v4"],
        default="v4",
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

    # Cluster completion breakdown
    cluster_counts = {1: 0, 4: 0, 9: 0}
    for r in results:
        for k, v in r["stats"].clusters_completed.items():
            cluster_counts[k] = cluster_counts.get(k, 0) + v
    total = sum(cluster_counts.values())
    print(
        f"  clusters:    1x1={cluster_counts.get(1,0)}  "
        f"2x2={cluster_counts.get(4,0)}  3x3={cluster_counts.get(9,0)}  total={total}"
    )

    stuck_count = sum(1 for r in results if r["empty_plan"])
    if stuck_count:
        print(f"  WARNING: {stuck_count}/{args.runs} games ended on planner returning 0 steps")


if __name__ == "__main__":
    main()
