"""Quick A/B benchmark: v3 vs v4 on representative boards.

One-off tool — not part of the test suite. Run with:
    python tools/compare_v3_v4_planners.py
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4


def _make_board(rows: int = 7, cols: int = 6, fill: str = "empty"):
    return [[fill] * cols for _ in range(rows)]


def _scenario_1x3_partial_row6() -> Tuple[str, List[List[str]], Dict[str, int]]:
    """3 pits in row 6 — top of a 3x3 just scrolled in (offscreen continuation)."""
    board = _make_board()
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    board[6][4] = "reachable_pit"
    return "1x3 partial @ row6 (user's scenario)", board, {"drill": 1, "bomb": 2}


def _scenario_1x3_partial_row0() -> Tuple[str, List[List[str]], Dict[str, int]]:
    """Mirror: 1x3 at top."""
    board = _make_board()
    board[0][2] = "reachable_pit"
    board[0][3] = "reachable_pit"
    board[0][4] = "reachable_pit"
    return "1x3 partial @ row0", board, {"drill": 1, "bomb": 2}


def _scenario_2x2_cluster() -> Tuple[str, List[List[str]], Dict[str, int]]:
    board = _make_board()
    board[5][2] = "reachable_pit"
    board[5][3] = "reachable_pit"
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    return "2x2 cluster (full, rows 5-6)", board, {"drill": 1, "bomb": 2}


def _scenario_3x3_cluster() -> Tuple[str, List[List[str]], Dict[str, int]]:
    board = _make_board()
    for r in range(2, 5):
        for c in range(1, 4):
            board[r][c] = "reachable_pit"
    return "3x3 cluster (full, rows 2-4)", board, {"drill": 1, "bomb": 2}


def _scenario_single_pit() -> Tuple[str, List[List[str]], Dict[str, int]]:
    board = _make_board()
    board[3][3] = "reachable_pit"
    return "single isolated pit", board, {"drill": 1, "bomb": 2}


def _scenario_5_pit_column() -> Tuple[str, List[List[str]], Dict[str, int]]:
    """Vertical strip — ideal for drill."""
    board = _make_board()
    for r in range(2, 7):
        board[r][3] = "reachable_pit"
    return "5-pit vertical column", board, {"drill": 1, "bomb": 2}


def _scenario_two_partial_rows() -> Tuple[str, List[List[str]], Dict[str, int]]:
    """3 pits in row 0 + 3 pits in row 6 — two separate partials."""
    board = _make_board()
    for c in (2, 3, 4):
        board[0][c] = "reachable_pit"
        board[6][c] = "reachable_pit"
    return "two 1x3 partials (row 0 + row 6)", board, {"drill": 2, "bomb": 2}


def _scenario_scattered() -> Tuple[str, List[List[str]], Dict[str, int]]:
    """Scattered pits — individual shovel dig each."""
    board = _make_board()
    for r, c in [(1, 0), (2, 5), (3, 2), (4, 4), (5, 1)]:
        board[r][c] = "reachable_pit"
    return "5 scattered pits", board, {"drill": 1, "bomb": 2}


def _scenario_3x3_with_partial_bottom() -> Tuple[str, List[List[str]], Dict[str, int]]:
    """Full 3x3 cluster plus partial 1x3 at bottom (the compound case)."""
    board = _make_board()
    for r in range(2, 5):
        for c in range(1, 4):
            board[r][c] = "reachable_pit"
    for c in (2, 3, 4):
        board[6][c] = "reachable_pit"
    return "3x3 cluster + 1x3 partial @ row6", board, {"drill": 1, "bomb": 2}


SCENARIOS = [
    _scenario_1x3_partial_row6,
    _scenario_1x3_partial_row0,
    _scenario_2x2_cluster,
    _scenario_3x3_cluster,
    _scenario_single_pit,
    _scenario_5_pit_column,
    _scenario_two_partial_rows,
    _scenario_scattered,
    _scenario_3x3_with_partial_bottom,
]


def _summarise(plan: Dict) -> Tuple[int, int, float, int]:
    stats = plan["stats"]
    return (
        stats["drills_used"],
        stats["bombs_used"],
        stats["shovel_cost"],
        stats["pits_collected"],
    )


def run_bench():
    header = f"{'scenario':<42} | {'planner':<4} | drill  bomb  shov  pits | steps"
    sep = "-" * len(header)
    print(header)
    print(sep)
    totals = {"v3": {"drill": 0, "bomb": 0, "shov": 0.0, "pits": 0},
              "v4": {"drill": 0, "bomb": 0, "shov": 0.0, "pits": 0}}
    for fn in SCENARIOS:
        name, board, items = fn()
        for planner, fnp in (("v3", plan_v3), ("v4", plan_v4)):
            plan = fnp([row[:] for row in board], shovels=20, items=dict(items))
            d, b, s, p = _summarise(plan)
            totals[planner]["drill"] += d
            totals[planner]["bomb"] += b
            totals[planner]["shov"] += s
            totals[planner]["pits"] += p
            steps = len(plan["steps"])
            print(f"{name:<42} | {planner:<4} | {d:^5} {b:^5} {s:^5.1f} {p:^5} | {steps}")
        print(sep)

    print()
    print(f"{'TOTALS':<42} | {'v3':<4} | {totals['v3']['drill']:^5} {totals['v3']['bomb']:^5} "
          f"{totals['v3']['shov']:^5.1f} {totals['v3']['pits']:^5}")
    print(f"{'TOTALS':<42} | {'v4':<4} | {totals['v4']['drill']:^5} {totals['v4']['bomb']:^5} "
          f"{totals['v4']['shov']:^5.1f} {totals['v4']['pits']:^5}")
    print()
    drill_saved = totals['v3']['drill'] - totals['v4']['drill']
    bomb_saved = totals['v3']['bomb'] - totals['v4']['bomb']
    shov_delta = totals['v4']['shov'] - totals['v3']['shov']
    pits_delta = totals['v4']['pits'] - totals['v3']['pits']
    print(f"v4 - v3 deltas: drill saved = {drill_saved:+d}, bomb saved = {bomb_saved:+d}, "
          f"extra shovels = {shov_delta:+.1f}, pits delta = {pits_delta:+d}")


if __name__ == "__main__":
    run_bench()
