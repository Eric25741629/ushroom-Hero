import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from miner.v3.board import normalize_board, canonicalize_in_place, count_pits, count_remaining_pits, floor7_open
from miner.v3.actions import apply_dig
from miner.v4.planner import plan_v4

board = [
    ["empty","dirt","unreachable_dirt","unreachable_dirt","unreachable_empty","unreachable_dirt"],
    ["empty","dirt","unreachable_dirt","unreachable_dirt","unreachable_rock","unreachable_empty"],
    ["empty","rock","unreachable_dirt","unreachable_dirt","unreachable_dirt","unreachable_dirt"],
    ["empty","dirt","unreachable_dirt","unreachable_rock","unreachable_dirt","unreachable_rock"],
    ["empty","dirt","unreachable_rock","unreachable_dirt","unreachable_rock","unreachable_dirt"],
    ["empty","empty","rock","unreachable_empty","unreachable_pit","unreachable_rock"],
    ["dirt","rock","unreachable_empty","unreachable_dirt","unreachable_dirt","unreachable_dirt"],
]
work = normalize_board(board)
canonicalize_in_place(work)
print("floor7_open initial:", floor7_open(work))
print("reachable/unreachable pits:", count_pits(work))
# simulate the only filtered action: dig (5,2)
nb = [r[:] for r in work]
apply_dig(nb, (5,2))
print("after dig (5,2): floor7_open=", floor7_open(nb), "reachable_pits=", count_pits(nb))
p = plan_v4(board, shovels=200, items={"drill":60,"bomb":600})
print("plan steps:", p["steps"], "f7:", p["floor7_open"])
