"""Eval-tool gates: final_v1 registered in sim/replay, KPI aggregation fields.

NOTE: repo 根目錄有 tools.py（模組，非套件），所以 tools/ 目錄下的評估腳本
要用它們自己的 sys.path 慣例直接 import。
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import compare_planners  # noqa: E402
import mining_sim_eval  # noqa: E402
import replay_real_boards  # noqa: E402


def test_final_v1_is_registered_in_sim_and_replay():
    assert "final_v1" in mining_sim_eval.PLANNERS
    assert "final_v1" in replay_real_boards.PLANNERS


def test_aggregate_reports_required_resource_and_latency_kpis():
    rows = [{
        "stats": type("S", (), {
            "score": 10, "pits": 4, "depth": 2, "cost": 2,
            "bombs_used": 1, "drills_used": 0,
            "clusters_completed": {4: 1},
        })(),
        "plan_times_ms": [10.0, 20.0, 30.0],
        "fallbacks": 0, "actions": 2, "empty_plan": False,
        "rejected": 0, "lost_pits": 0, "unfinished_clusters": 0,
    }]
    result = compare_planners.agg(rows, equal_item_weight=3.0)
    for key in (
        "clusters", "pits_per_shovel", "pits_per_item", "pits_per_equal_cost",
        "lost_pits", "unfinished_clusters", "rejected", "plan_ms_p95",
        "plan_ms_p99", "plan_ms_max",
    ):
        assert key in result


def test_get_known_board_is_a_readonly_slice_of_the_tape():
    sim = mining_sim_eval.MiningSim(rng=random.Random(1))
    known = sim.get_known_board(21)
    assert 7 <= len(known) <= 21
    assert len(known[0]) == 6
    known[0][0] = "mutated"
    assert sim.tape[sim.viewport][0] != "mutated"

def test_step_mode_executes_at_most_one_action_per_plan_call():
    r = mining_sim_eval.play_one_game(
        seed=5, max_iter=60, planner="v1",
        starting_inv={"pickaxe": 20, "bomb": 0, "drill": 0},
        exec_mode="step",
    )
    assert r["actions"] <= r["plan_calls"]


def test_pickaxe_zero_terminates_immediately_even_with_items_left():
    r = mining_sim_eval.play_one_game(
        seed=5, max_iter=50, planner="v1",
        starting_inv={"pickaxe": 0, "bomb": 50, "drill": 50},
    )
    assert r["iters"] == 0
    assert r["actions"] == 0


def test_aggregate_normalizes_counters_per_game():
    def _row(clusters, lost):
        return {
            "stats": type("S", (), {
                "score": 10, "pits": 4, "depth": 2, "cost": 2,
                "bombs_used": 1, "drills_used": 0,
                "clusters_completed": dict(clusters),
            })(),
            "plan_times_ms": [10.0],
            "fallbacks": 0, "actions": 2, "empty_plan": False,
            "rejected": 0, "lost_pits": lost, "unfinished_clusters": 0,
        }
    result = compare_planners.agg([_row({1: 2}, 4), _row({1: 4}, 0)])
    assert result["clusters"] == 3.0  # (2+4)/2 局，每局平均
    assert result["lost_pits"] == 2.0  # (4+0)/2 局


def test_run_paired_keeps_all_planners_on_the_same_seed_set():
    rows = compare_planners.run_paired(
        ["v1", "v4"], 2, 5, 30, {"pickaxe": 15, "bomb": 0, "drill": 0},
        time_cap_s=600.0,
    )
    assert len(rows["v1"]) == len(rows["v4"]) == 2


# --- Bomb off-screen (world-coordinate footprint) alignment ----------------
# The planner (miner/final_v1) counts bomb hits on known cells BELOW the 7-row
# viewport; the sim must execute the same physics or every 21-row evaluation is
# corrupted (prediction != execution). drill stays viewport-bounded.

def _controlled_sim(fill="unreachable_dirt", height=15):
    """A MiningSim whose tape/inventory/bookkeeping are fully author-controlled
    (no random terrain), so a single bomb/drill can be asserted precisely."""
    sim = mining_sim_eval.MiningSim(rng=random.Random(0))
    sim.tape = [[fill] * mining_sim_eval.COLS for _ in range(height)]
    sim.viewport = 0
    sim.clusters = []
    sim.cell_to_cluster = {}
    sim.inv = {"pickaxe": 100, "bomb": 3, "drill": 3}
    sim.stats = mining_sim_eval.SimStats()
    return sim


def test_bomb_opens_world_cell_below_viewport_and_collects_pit():
    sim = _controlled_sim()
    # Bomb centre at view row 5 (reachable air); its cross extension reaches
    # world row 7 = one row BELOW the 7-row viewport bottom (row 6).
    sim.tape[5][2] = "empty"
    sim.tape[7][2] = "unreachable_pit"
    cluster = mining_sim_eval.Cluster(cells={(7, 2)}, total=1)
    sim.clusters = [cluster]
    sim.cell_to_cluster = {(7, 2): cluster}

    res = sim._do_bomb(5, 2)

    assert res["ok"] is True
    # World row 7 (below viewport) must be opened by the bomb.
    assert sim.tape[7][2] == "dug_pit"
    # Its single-cell cluster completes -> pit collected into stats.
    assert sim.stats.pits == 1
    assert sim.stats.bombs_used == 1


def test_drill_does_not_reach_below_viewport():
    sim = _controlled_sim()
    # Drill centre at view row 3; a viewport-bounded drill digs its column down
    # to the 7-row bottom (row 6) only -- never into world rows 7/8.
    sim.tape[3][2] = "empty"
    sim.tape[7][2] = "unreachable_pit"
    sim.tape[8][2] = "unreachable_pit"

    res = sim._do_drill(3, 2)

    assert res["ok"] is True
    # Below-viewport world cells are never DUG by the drill (post-scroll
    # reachability recompute may promote unreachable_->reachable_, but the cell
    # must remain an undug pit -- not "dug_pit" -- and collect nothing).
    assert mining_sim_eval.is_pit(sim.tape[7][2])
    assert mining_sim_eval.is_pit(sim.tape[8][2])
    assert sim.stats.pits == 0


def test_bomb_opened_below_cell_is_consistent_after_scroll():
    sim = _controlled_sim()
    sim.tape[5][2] = "empty"
    sim.tape[7][2] = "unreachable_rock"  # below viewport hard cell

    sim._do_bomb(5, 2)

    # The bomb opened the below-viewport hard cell in the world tape.
    assert sim.tape[7][2] == "empty"
    # get_board() always derives from the tape, so scrolling into the bombed
    # region shows the opened cell -- board and world stay consistent.
    board = sim.get_board()
    for i in range(mining_sim_eval.ROWS):
        assert board[i] == sim.tape[sim.viewport + i]


def test_final_v1_receives_exec_profile_matching_exec_mode(monkeypatch):
    # step mode must forward exec_profile="step" so the planner's RHO_ACTION_STEP
    # KPI alignment is actually exercised (was hard-wired to plan/rho=0 before).
    seen = []

    def _spy(board, **kwargs):
        seen.append(kwargs.get("exec_profile"))
        return {"steps": []}

    monkeypatch.setitem(mining_sim_eval.PLANNERS, "final_v1", _spy)
    mining_sim_eval.play_one_game(
        seed=1, max_iter=3, planner="final_v1",
        starting_inv={"pickaxe": 10, "bomb": 0, "drill": 0},
        exec_mode="step",
    )
    assert seen and all(p == "step" for p in seen)
