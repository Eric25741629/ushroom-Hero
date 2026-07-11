"""WS final_v1 projection: 21-row known board, action-aware valid targets,
dispatch, seven-row degradation, shadow isolation, and v1 fallback."""
from ws_token import mining, mining_adapter


def _block(depth, col, config_id, count=1, is_reward=0):
    return mining.MineBlock(depth * 100 + col, col, depth, config_id, count, is_reward)


def _board(*, baseline=105, area_info=None, actives=None, blocks=None):
    return mining.MineBoard(
        max_num=20, next_time=0, area=0, baseline=baseline,
        actives=list(actives or []), area_info=dict(area_info or {}),
        blocks=list(blocks or []), holes=[],
    )


def test_known_board_extends_to_covered_area_info_but_caps_at_21_rows(monkeypatch):
    board = _board(area_info={14: 1, 15: 2, 16: 3})
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda depth, col, info: 201)
    projected = mining_adapter.build_final_v1_input(board, {"pickaxe": 5, "bomb": 1, "drill": 1})
    assert len(projected["board"]) == 21
    assert projected["visible_rows"] == 7


def test_raw_offscreen_pit_overlays_static_terrain(monkeypatch):
    top = mining_adapter.viewport_top_depth(105)
    pit = _block(top + 8, 3, mining.TERRAIN_PIT, count=1, is_reward=1)
    board = _board(blocks=[pit])
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda depth, col, info: 201)
    projected = mining_adapter.build_final_v1_input(board, {})
    assert projected["board"][8][2] == "unreachable_pit"


def test_valid_targets_distinguish_pickaxe_frontier_from_item_air_placement():
    top = mining_adapter.viewport_top_depth(105)
    solid = _block(top + 1, 3, mining.TERRAIN_DIRT, count=1)
    air = _block(top, 3, mining.TERRAIN_DIRT, count=0)
    board = _board(actives=[solid.block_id], blocks=[solid, air])
    valid = mining_adapter.build_final_v1_input(board, {})["valid_targets"]
    assert ("dig", "pickaxe", 1, 2) in valid
    assert ("use", "bomb", 0, 2) in valid
    assert ("use", "drill", 0, 2) in valid
    assert ("dig", "pickaxe", 0, 2) not in valid


def test_plan_final_v1_maps_only_legal_first_step_to_ws_block_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "miner.final_v1.plan_final_v1",
        lambda *args, **kwargs: captured.update(kwargs) or {
            "steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}],
            "score_breakdown": {"total": 1},
        },
    )
    board = _board(actives=[10103])
    result = mining_adapter.plan(board, {"pickaxe": 5}, planner_version="final_v1")
    assert captured["visible_rows"] == 7
    assert result["ws_steps"][0]["block_id"] == mining_adapter.grid_pos_to_block_id(105, 1, 2)


def test_incomplete_known_projection_degrades_to_seven_rows(monkeypatch):
    board = _board(area_info={14: 1, 16: 3})
    top = mining_adapter.viewport_top_depth(board.baseline)
    monkeypatch.setattr(
        mining_adapter.mine_terrain,
        "terrain_at",
        lambda depth, col, info: 201 if depth < top + 7 else None,
    )
    projected = mining_adapter.build_final_v1_input(board, {"pickaxe": 5})
    assert len(projected["board"]) == 7
    assert projected["projection_mode"] == "visible_only"


def test_empty_final_v1_result_falls_back_to_v1(monkeypatch):
    calls = []

    def fake_named(name, projected, inventory):
        calls.append(name)
        if name == "final_v1":
            return {"steps": []}
        return {"steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}]}

    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(_board(), {"pickaxe": 5}, planner_version="final_v1")
    assert calls == ["final_v1", "v1"]
    assert result["planner_source"] == "v1_fallback"


def test_shadow_exception_is_logged_in_result_and_primary_plan_survives(monkeypatch):
    expected_primary_steps = [{"type": "dig", "target": (1, 2), "step_cost": 1}]

    def fake_named(name, projected, inventory):
        if name == "final_v1":
            raise RuntimeError("shadow boom")
        return {"steps": expected_primary_steps}

    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(
        _board(), {"pickaxe": 5}, planner_version="v1",
        shadow_planner_version="final_v1",
    )
    assert result["shadow"]["ok"] is False
    assert result["ws_steps"][0]["target"] == expected_primary_steps[0]["target"]
