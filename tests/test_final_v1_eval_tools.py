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
