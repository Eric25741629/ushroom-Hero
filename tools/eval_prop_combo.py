"""Monte-Carlo A/B: legacy single-shot prop selector vs the new multi-step combo.

Quantifies the decision-quality gap between the OLD ``prop_step_for_pit`` (single
greedy shot, unconditional bomb-before-drill bias) and the NEW combo planner
(``prop_combo_for_pits``: bounded DFS maximising joint pit coverage across up to
3 props). Both sides run the SAME "re-plan every step" loop and the SAME session
budget (max 3 props, inventory bomb=3 / drill=2) on the SAME random boards, so the
only variable is the selector.

Board model (calibrated to the real mine): a depth band of 13 rows (visible 7 +
6 below the window) x 6 columns; 2-4 square ore clusters (1x1/2x2/3x3 at
66%/18%/17%) as uncollected pits; 5-15 already-dug air cells near the top (a mined
corridor) as prop placements. Only pits + air drive prop choice, so undug filler
terrain is omitted.

  python tools/eval_prop_combo.py --runs 2000 --seed 42
"""
from __future__ import annotations

import argparse
import io
import os
import random
import statistics
import sys
import time
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from ws_token import mining_adapter as ma  # noqa: E402

BASELINE = 100000
TOP = ma.viewport_top_depth(BASELINE)     # depth of grid row 0
COLS = 6
BAND_ROWS = 13                            # visible 7 + 6 below the viewport
AIR_ROWS = 7                              # corridor is dug near the top
MAX_PROPS = 3                             # session budget (both sides)
START_INV = {"bomb": 3, "drill": 2}
PICKAXE_PER_PIT = 2.5                     # a collected pit else costs ~2.5 shovels

CLUSTER_SHAPES = [1, 2, 3]
CLUSTER_WEIGHTS = [66, 18, 17]            # 1x1 / 2x2 / 3x3 (calibrated distribution)


# --- board generation ---------------------------------------------------------

def _gen_board(rng: random.Random) -> Tuple[set, frozenset]:
    """(pit cells, air cells) in absolute (depth, col). Pits from square clusters,
    air from a top-of-band mined corridor that never overlaps a pit."""
    pits: set = set()
    for _ in range(rng.randint(2, 4)):
        s = rng.choices(CLUSTER_SHAPES, weights=CLUSTER_WEIGHTS, k=1)[0]
        r0 = rng.randint(0, BAND_ROWS - s)
        c0 = rng.randint(0, COLS - s)
        for dr in range(s):
            for dc in range(s):
                pits.add((TOP + r0 + dr, c0 + dc))

    air: set = set()
    want = rng.randint(5, 15)
    attempts = 0
    while len(air) < want and attempts < 200:
        attempts += 1
        cell = (TOP + rng.randint(0, AIR_ROWS - 1), rng.randint(0, COLS - 1))
        if cell not in pits:
            air.add(cell)
    return pits, frozenset(air)


def _mk_board(pits: set, air: set) -> SimpleNamespace:
    """A fake MineBoard (SimpleNamespace) from pit/air sets — same shape the
    selector reads (blocks with x/y/count/config_id/is_reward, board baseline)."""
    blocks = [SimpleNamespace(block_id=d * 100 + c + 1, x=c + 1, y=d,
                              config_id=ma.TERRAIN_PIT, count=1, is_reward=1)
              for (d, c) in pits]
    blocks += [SimpleNamespace(block_id=d * 100 + c + 1, x=c + 1, y=d,
                               config_id=ma.TERRAIN_DIRT, count=0, is_reward=0)
               for (d, c) in air]
    return SimpleNamespace(baseline=BASELINE, actives=[], area_info={},
                           blocks=blocks, holes=[])


# --- legacy selector (single shot, bomb-priority) — kept out of production -----

def _legacy_single(board: SimpleNamespace, inv: Dict[str, int], *,
                   min_pits: int = 2) -> Optional[Dict]:
    """Faithful copy of the OLD prop_step_for_pit: one prop, bomb tried before
    drill, highest single-use hit wins. Lives here so production carries only the
    combo planner."""
    pits, air = ma._pits_and_air(board)
    if len(pits) < min_pits or not air:
        return None
    top = ma.viewport_top_depth(int(getattr(board, "baseline", 0) or 0))

    def _step(item, ad, ac):
        return {"type": "use", "item": item, "block_id": ad * 100 + ac + 1,
                "row": ad - top, "col": ac, "step_cost": 2.99}

    if int(inv.get("bomb", 0) or 0) > 0:
        best = None
        for (ad, ac) in air:
            hit = len(ma._bomb_blast_cells(ad, ac) & pits)
            if hit >= min_pits and (best is None or hit > best[0]):
                best = (hit, ad, ac)
        if best is not None:
            return _step("bomb", best[1], best[2])
    if int(inv.get("drill", 0) or 0) > 0:
        best = None
        for (ad, ac) in air:
            hit = len(ma._drill_clear_cells(ad, ac, pits))
            if hit >= min_pits and (best is None or hit > best[0]):
                best = (hit, ad, ac)
        if best is not None:
            return _step("drill", best[1], best[2])
    return None


# --- stepwise simulation ------------------------------------------------------

def _apply(step: Dict, pits: set, air: set) -> Tuple[int, str]:
    """Apply a prop step to (pits, air) in place; return (pits_cleared, item)."""
    item = step["item"]
    depth = int(step["block_id"]) // 100
    col = int(step["col"])
    if item == "bomb":
        blast = ma._bomb_blast_cells(depth, col)
        cleared = blast & pits
        freed = blast
    else:
        cleared = ma._drill_clear_cells(depth, col, pits)
        freed = cleared
    pits -= cleared
    air |= freed
    return len(cleared), item


def _run_side(selector: Callable, pits0: set, air0: frozenset,
              timer: Optional[List[float]] = None
              ) -> Tuple[int, int, Optional[Tuple[str, int]]]:
    """Re-plan/execute until the selector declines or the budget runs out.
    Returns (props_used, pits_hit, first_step_signature)."""
    pits, air = set(pits0), set(air0)
    inv = dict(START_INV)
    props = hits = 0
    first: Optional[Tuple[str, int]] = None
    while props < MAX_PROPS:
        board = _mk_board(pits, air)
        t0 = time.perf_counter()
        step = selector(board, inv)
        if timer is not None:
            timer.append((time.perf_counter() - t0) * 1000.0)
        if step is None:
            break
        if first is None:
            first = (step["item"], int(step["block_id"]))
        cleared, item = _apply(step, pits, air)
        inv[item] -= 1
        hits += cleared
        props += 1
    return props, hits, first


# --- aggregation --------------------------------------------------------------

class _Stat:
    def __init__(self) -> None:
        self.fired = 0
        self.props = 0
        self.hits = 0
        self.combo = 0          # boards using >= 2 props
        self.per_board_hits: List[int] = []

    def add(self, props: int, hits: int) -> None:
        if props >= 1:
            self.fired += 1
        if props >= 2:
            self.combo += 1
        self.props += props
        self.hits += hits
        self.per_board_hits.append(hits)


def _row(name: str, s: _Stat, runs: int) -> str:
    hit_per_prop = (s.hits / s.props) if s.props else 0.0
    hit_per_board = (s.hits / runs) if runs else 0.0
    pickaxe = hit_per_board * PICKAXE_PER_PIT
    return (f"{name:<8} fire%={100 * s.fired / runs:5.1f}  "
            f"props/board={s.props / runs:5.3f}  "
            f"pits/prop={hit_per_prop:5.3f}  "
            f"pits/board={hit_per_board:5.3f}  "
            f"combo%={100 * s.combo / runs:5.1f}  "
            f"pickaxe~={pickaxe:5.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B: legacy vs multi-step prop combo")
    ap.add_argument("--runs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    new_sel = lambda b, inv: ma.prop_step_for_pit(  # noqa: E731
        b, inv, allow_bomb=True, allow_drill=True)
    old_sel = lambda b, inv: _legacy_single(b, inv)  # noqa: E731

    old_stat, new_stat = _Stat(), _Stat()
    changed = 0
    timer: List[float] = []

    for _ in range(args.runs):
        pits, air = _gen_board(rng)
        op, oh, of = _run_side(old_sel, pits, air)
        np_, nh, nf = _run_side(new_sel, pits, air, timer=timer)
        old_stat.add(op, oh)
        new_stat.add(np_, nh)
        if of != nf:
            changed += 1

    print(f"prop-combo A/B  runs={args.runs} seed={args.seed} "
          f"(budget {MAX_PROPS} props, inv bomb={START_INV['bomb']} "
          f"drill={START_INV['drill']})")
    print(_row("legacy", old_stat, args.runs))
    print(_row("combo", new_stat, args.runs))
    print(f"decision-change%={100 * changed / args.runs:5.1f}  "
          f"(first step differs)")
    if timer:
        print(f"prop_combo timing: avg={statistics.mean(timer):.3f}ms  "
              f"max={max(timer):.3f}ms  calls={len(timer)}")


if __name__ == "__main__":
    main()
