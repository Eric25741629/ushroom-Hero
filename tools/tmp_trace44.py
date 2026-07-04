import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from miner.v3.board import normalize_board, canonicalize_in_place, floor7_open, is_reachable_air, is_frontier_diggable
from miner.v3.actions import enumerate_dig_actions, enumerate_item_actions
from miner.v4.planner import _no_pit_dig_filter, _classify_strategy

board = [["unreachable_empty"]*6 for _ in range(7)]
board[1][1] = "empty"
board[5][1] = "unreachable_rock"
work = normalize_board(board)
canonicalize_in_place(work)
print("strategy:", _classify_strategy(work))
print("floor7_open:", floor7_open(work))
print("reachable_air cells:", [(r,c) for r in range(7) for c in range(6) if is_reachable_air(work[r][c])])
print("frontier diggable:", [(r,c) for r in range(7) for c in range(6) if is_frontier_diggable(work, r, c)])
print("enumerate_dig_actions:", [a["pos"] for a in enumerate_dig_actions(work)])
print("no_pit_dig_filter:", [a["pos"] for a in _no_pit_dig_filter(work)])
print("item actions:", [(a["item"],a["pos"]) for a in enumerate_item_actions(work, {"drill":60,"bomb":600})])
from miner.v3.actions import apply_drill
nb = [r[:] for r in work]
apply_drill(nb, (1,1))
print("after drill(1,1) floor7_open:", floor7_open(nb))
